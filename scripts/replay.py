"""마이그레이션 이력을 빈 DB 하나(PostgreSQL, MySQL, MariaDB, Oracle)에 순서대로 적용하며 단계마다 카탈로그 스냅샷을 뜬다.

사용법: python3 replay.py <레포 또는 마이그레이션 디렉터리> --out out/timeline [--format flyway] [--db postgres] [--image 이미지]
        [--ignore-repeatable] [--step-timeout 초] [--lang ko|en]
        python3 replay.py --cleanup        (남은 재생용 컨테이너·네트워크 정리)
--lang 은 steps.json 의 경고·오류 문구와 터미널 출력의 언어다.
형식별로 단계 목록을 만드는 일은 sources.py 의 어댑터가 한다.

결과
  steps.json           경고(warnings)·안내(notes)와 단계 목록: 순번, 버전, 설명, 파일, SQL(비밀번호 가림), DML 여부, 상태, 오류, 경고,
                       objects(스냅샷에 없는 트리거·함수·프로시저·패키지·시퀀스 등의 추가·삭제·변경·INVALID)
  snapshots/NNN.json   단계별 스냅샷. 000.json 은 빈 DB(V0)다.
모든 스냅샷에 같은 replay_id 가 붙는다. diff.py 는 이 값이 같으면 oid·attnum 으로 이름 변경을 확정한다.
PostgreSQL 은 카탈로그의 oid·attnum 을, 다른 엔진은 단계 SQL 의 이름 변경 문으로 이어 붙인 id(idmap.py)를 쓴다.
컨테이너는 네트워크 없이, 메모리·CPU·프로세스 수 제한과 정리용 라벨을 붙여 띄운다(snapshot.py 의 limits).
단계 하나가 제한 시간(기본 300초)을 넘기면 그 단계를 실패로 표시하고 재생을 멈춘다. 마이그레이션 디렉터리는 읽기만 한다.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engines  # noqa: E402
from engines import diff_objects, no_objects  # noqa: E402
from i18n import L, add_argument, early, init, plural  # noqa: E402
from idmap import IdMap, hints  # noqa: E402
from snapshot import (DEFAULT_STEP_TIMEOUT, STEP_TIMEOUT_ENV, StepTimeout, cleanup, install_signal_handlers, psql, run,  # noqa: E402
                      set_step_timeout, timeout_text)
from sources import FORMATS, find_git_snapshots, find_sql_files, find_steps, load  # noqa: E402,F401
from scriptsplit import normalize  # noqa: E402
from sqltext import head, mask_secrets, mask_snapshot, scrub, statement_spans, statements  # noqa: E402

MISSING_ROLE = re.compile(r'role "((?:[^"]|"")+)" does not exist')
BUILTIN_ROLES = {"public", "postgres", "current_user", "session_user", "current_role"}
DML_HEADS = ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE")
NON_TX_HEADS = ("CREATE DATABASE", "DROP DATABASE", "ALTER SYSTEM", "CREATE TABLESPACE", "DROP TABLESPACE", "VACUUM")
TX_CONTROL = ("BEGIN", "COMMIT", "ROLLBACK", "START TRANSACTION", "END")
OUTPUTS = ("snapshots", "diffs", "events.json", "lineage.json", "steps.json", "snapshots_view.json", "check.json", "timeline.check.png")
EXTENSION_STMT = re.compile(r'(?:CREATE\s+EXTENSION(?:\s+IF\s+NOT\s+EXISTS)?|COMMENT\s+ON\s+EXTENSION|ALTER\s+EXTENSION'
                            r'|DROP\s+EXTENSION(?:\s+IF\s+EXISTS)?)\s+"?([\w-]+)"?', re.I)


def role_name(raw):
    """GRANT 등에 쓰인 role 표기를 실제 이름으로. 따옴표가 있으면 대소문자를 그대로 둔다."""
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('""', '"')
    return raw.lower()


def referenced_roles(sqls):
    """GRANT·OWNER TO·POLICY 에서 참조하지만 이력 안에서 만들지 않는 role 이름. pg_ 로 시작하는 기본 role 은 뺀다."""
    created, used = set(), set()
    ROLE = r'("(?:[^"]|"")+"|\w+)'
    for sql in sqls:
        for stmt in statements(sql):
            created |= {role_name(x) for x in re.findall(rf"\bCREATE\s+(?:ROLE|USER|GROUP)\s+{ROLE}", stmt, re.I)}
            if re.match(r"(GRANT|CREATE\s+POLICY|ALTER\s+DEFAULT\s+PRIVILEGES)\b", stmt, re.I):
                m = re.search(r"\bTO\s+(.+)$", stmt, re.I | re.S)
                if m:
                    targets = re.sub(r"\b(WITH|USING|GRANTED\s+BY)\b.*$", "", m.group(1), flags=re.I | re.S)
                    used |= {role_name(x) for x in re.findall(ROLE, targets)
                             if x.upper() not in ("GROUP", "ROLE")}
            used |= {role_name(x) for x in re.findall(rf"\bOWNER\s+TO\s+{ROLE}", stmt, re.I)}
    return sorted(r for r in used - created
                  if r.lower() not in BUILTIN_ROLES and not r.startswith("pg_"))


def analyze(sql, engine="postgres"):
    """한 파일의 DML 포함 여부, 트랜잭션으로 감쌀 수 있는지, 재생 전 경고. 트랜잭션 DDL 이 없는 엔진은 언제나 False 다."""
    dialect = {"mysql": "mysql", "mariadb": "mysql", "oracle": "oracle"}.get(engine, "postgres")
    stmts = statements(normalize(sql, dialect), dialect)
    heads = [head(s) for s in stmts]
    starts = lambda h, words: any((h + " ").startswith(w + " ") for w in words)
    dml = any(starts(h, DML_HEADS) or (starts(h, ("COPY",)) and re.search(r"\bFROM\b", st, re.I))
              or (starts(h, ("WITH",)) and re.search(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b",
                                                      re.sub(r"\bFOR\s+(NO\s+KEY\s+)?UPDATE\b", " ", st, flags=re.I), re.I))
              for h, st in zip(heads, stmts))
    non_tx = any(starts(h, NON_TX_HEADS + TX_CONTROL) or re.search(r"\bCONCURRENTLY\b", st, re.I)
                 or re.match(r"ALTER TYPE \S+ ADD VALUE", st, re.I) for h, st in zip(heads, stmts))
    warnings = []
    if "${" in sql:
        warnings.append(L("Flyway placeholder(${...})가 있습니다. 치환하지 않고 그대로 적용했습니다.",
                          "Flyway placeholders (${...}) found. They were applied as written, not substituted."))
    if engine == "postgres" and re.search(r"^\s*\\", sql, re.M):
        warnings.append(L("psql 메타명령(\\로 시작하는 줄)이 있습니다. Flyway 는 이 줄을 실행하지 않습니다.",
                          "psql meta-commands (lines starting with \\) found. Flyway does not run these lines."))
    return dml, not non_tx and engine == "postgres", warnings


def drop_unavailable_extensions(sql, available):
    """이미지에 없는 확장을 다루는 CREATE/COMMENT ON/ALTER/DROP EXTENSION 문을 주석으로 바꾼다. 문자열·함수 본문 안은 건드리지 않는다.

    @return (바꾼 SQL, 건너뛴 확장 이름 목록)
    """
    out, last, skipped = [], 0, []
    for start, end, stmt in statement_spans(sql):
        m = EXTENSION_STMT.match(stmt)
        if not m or m.group(1).lower() in available:
            continue
        skipped.append(m.group(1))
        out += [sql[last:start], "-- [schema-timeline] " + L("건너뜀", "skipped") + ": " + " ".join(sql[start:end].split()) + "\n"]
        last = end
    out.append(sql[last:])
    return "".join(out), sorted(set(skipped))


def snapshot_doc(engine, cid, db, replay_id, source, schema, secrets=(), ids=None, step_hints=None):
    """카탈로그를 떠서 비밀값을 가리고 DDL 을 붙인다. ids(IdMap)가 있으면 테이블·컬럼 id 를 이어 붙인다."""
    doc = engine.catalog(cid, db, schema)
    doc["engine"] = engine.name
    doc = engine.add_ddl(mask_snapshot(doc, secrets))
    if ids is not None:
        ids.assign(doc, step_hints)
    doc["source"] = source
    doc["replay_id"] = replay_id
    return doc


def prepare_db(engine, cid, db, schema, prelude):
    """재생용 DB 를 만들고 prelude(플랫폼 기본 스키마의 대역)를 적용한다. 쓸 수 있는 확장 이름 집합을 돌려준다."""
    engine.create_db(cid, db, schema)
    if prelude:
        try:
            proc = engine.apply(cid, db, prelude.read_text(), single_tx=False, schema=schema)
        except StepTimeout as e:
            raise SystemExit(timeout_text(prelude.name, e.seconds))
        if proc.returncode != 0:
            raise SystemExit(L(f"{prelude.name} 를 적용하지 못했습니다:\n{proc.stderr.strip()}", f"Could not apply {prelude.name}:\n{proc.stderr.strip()}"))
    if not engine.has_extensions:
        return set()
    return set(psql(cid, db, "SELECT string_agg(name, ',') FROM pg_available_extensions;").strip().lower().split(","))


def fold_for(engine):
    """따옴표 없는 이름을 카탈로그 표기로 바꾸는 함수. Oracle 은 대문자, PostgreSQL 은 소문자다."""
    return {"oracle": str.upper, "postgres": str.lower}.get(engine.name, lambda x: x)


def replay_once(steps, image, roles, schema, prelude=None, skip_unavailable_extensions=False, engine=None):
    """컨테이너 하나에서 처음부터 끝까지 재생한다. 없는 role 때문에 실패하면 그 role 이름을 돌려준다.

    @return (snapshots, step_rows, missing_role)
    """
    engine = engine or engines.get("postgres")
    db = engine.db_for(schema)
    cid = engine.start(image)
    replay_id = uuid.uuid4().hex
    ids = None if engine.stable_ids else IdMap(case_insensitive_columns=engine.name in ("mysql", "mariadb"))
    try:
        for r in roles if engine.has_roles else []:
            run(["docker", "exec", "-i", cid, "psql", "-X", "-U", "postgres", "-q", "-c",
                 'CREATE ROLE "{}" NOLOGIN'.format(r.replace('"', '""'))], check=False)
        available = prepare_db(engine, cid, db, schema, prelude)
        snaps = [snapshot_doc(engine, cid, db, replay_id, "V0", schema, ids=ids)]
        objs = engine.objects(cid, db, schema)
        rows = []
        failed = False
        secrets = []
        for i, s in enumerate(steps, 1):
            raw = s["text"] if "text" in s else s["path"].read_text(encoding="utf-8-sig")
            name = s.get("file") or s["path"].name
            masked, found = mask_secrets(raw, engine.dialect)
            secrets += found
            dml, tx, warnings = analyze(raw, engine.name)
            row = {"index": i, "version": s["version"], "label": s["label"], "description": s["description"],
                   "file": name, "sql": masked, "dml": dml,
                   "status": "applied", "error": None, "warnings": warnings, "objects": no_objects()}
            if failed:
                row.update(status="not_run", sql=masked)
                rows.append(row)
                continue
            if s.get("full"):
                engine.drop_db(cid, db, schema)
                prepare_db(engine, cid, db, schema, prelude)
                if ids is not None:
                    ids = IdMap(case_insensitive_columns=ids.ci)
            sql = raw
            if skip_unavailable_extensions:
                sql, gone = drop_unavailable_extensions(raw, available)
                if gone:
                    row["warnings"].append(L(f"이미지에 없는 확장이라 건너뛰었습니다: {', '.join(gone)}. 이 확장에 기대는 객체는 재생에 실패할 수 있습니다.",
                                             f"Skipped extensions that the image does not have: {', '.join(gone)}. Objects that depend on them may fail to replay."))
            sql, prep = engine.prepare_sql(sql)
            row["warnings"] += prep
            try:
                proc = engine.apply(cid, db, sql, single_tx=tx, schema=schema)
            except StepTimeout as e:
                if not engine.transactional_ddl:
                    row["warnings"].append(L(f"{engine.label} 의 DDL 은 트랜잭션으로 묶이지 않아, 시간 초과 전까지 끝난 문장은 적용된 상태일 수 있습니다.",
                                             f"{engine.label} DDL is not transactional, so statements that finished before the timeout may stay applied."))
                row.update(status="failed", error=timeout_text(L("단계 ", "Step ") + row["label"], e.seconds))
                rows.append(row)
                failed = True
                continue
            if proc.returncode != 0:
                err = engine.error_text(proc.stderr)
                m = MISSING_ROLE.search(err) if engine.has_roles else None
                if m and m.group(1).replace('""', '"') not in roles:
                    return None, None, m.group(1).replace('""', '"')
                if not engine.transactional_ddl:
                    row["warnings"].append(L(f"{engine.label} 의 DDL 은 트랜잭션으로 묶이지 않아, 실패한 문장 앞까지는 적용된 상태입니다.",
                                         f"{engine.label} DDL is not transactional, so statements before the failed one stay applied."))
                row.update(status="failed", error=scrub(err, found))
                rows.append(row)
                failed = True
                continue
            row["warnings"] += engine.apply_warnings(proc)
            now = engine.objects(cid, db, schema)
            row["objects"] = diff_objects(objs, now)
            objs = now
            rows.append(row)
            snaps.append(snapshot_doc(engine, cid, db, uuid.uuid4().hex if s.get("full") else replay_id, name, schema, secrets,
                                      ids, hints(raw, fold_for(engine), engine.dialect, db) if ids is not None else None))
        if engine.name == "postgres" and not snaps[-1]["tables"]:
            others = psql(cid, db, "SELECT string_agg(n.nspname || '(' || count || ')', ', ') FROM ("
                          "SELECT c.relnamespace, count(*) FROM pg_class c WHERE c.relkind IN ('r','p') GROUP BY 1) x "
                          "JOIN pg_namespace n ON n.oid = x.relnamespace "
                          "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast');").strip()
            if others:
                print(L(f"  안내: {schema} 스키마에 테이블이 없습니다. 테이블이 있는 스키마: {others}. --schema 로 지정하세요.",
                        f"  Note: schema {schema} has no tables. Schemas with tables: {others}. Pass one with --schema."),
                      file=sys.stderr)
        return snaps, rows, None
    finally:
        engine.stop(cid)


def main():
    early()
    parser = argparse.ArgumentParser()
    parser.add_argument("migrations", nargs="?", type=Path, help=L("레포 루트나 마이그레이션 디렉터리", "Repo root or migrations directory"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--db", choices=engines.NAMES, default="postgres", help=L("재생할 DB 엔진", "Database engine to replay on"))
    parser.add_argument("--image", help=L("재생 이미지(기본: 엔진마다 다름)", "Replay image (default depends on the engine)"))
    parser.add_argument("--schema", help=L("스냅샷을 뜰 스키마. PostgreSQL 은 스키마(기본 public), MySQL·MariaDB 는 데이터베이스(기본 replay), "
                                           "Oracle 은 사용자(기본 REPLAY)",
                                           "Schema to snapshot. PostgreSQL: schema (default public), MySQL and MariaDB: database (default replay), "
                                           "Oracle: user (default REPLAY)"))
    parser.add_argument("--format", choices=FORMATS, default="flyway",
                        help=L("flyway: V<버전>__<설명>.sql, sqlfiles: <번호>__<설명>.sql 을 번호 순으로(init 먼저), "
                               "git-snapshots: 전체 스키마 파일(--file)의 git 리비전마다 빈 DB 에 적용, "
                               "supabase: supabase/migrations, prisma: prisma/migrations, drizzle: drizzle/ 와 meta/_journal.json",
                               "flyway: V<version>__<description>.sql, sqlfiles: <number>__<description>.sql in number order (init first), "
                               "git-snapshots: apply each git revision of a full schema file (--file) to an empty database, "
                               "supabase: supabase/migrations, prisma: prisma/migrations, drizzle: drizzle/ and meta/_journal.json"))
    parser.add_argument("--file", help=L("git-snapshots 에서 쓸 전체 스키마 파일의 레포 기준 경로", "Path of the full schema file for git-snapshots, relative to the repo"))
    parser.add_argument("--ignore-repeatable", action="store_true",
                        help=L("R__ 와 SQL 콜백 파일을 빼고 진행한다", "Skip R__ and SQL callback files"))
    parser.add_argument("--step-timeout", type=float, metavar="SEC",
                        help=L(f"단계 하나를 적용하는 제한 시간(초, 기본 {DEFAULT_STEP_TIMEOUT}, 0 은 제한 없음). 환경 변수 {STEP_TIMEOUT_ENV}",
                               f"Time limit in seconds for applying one step (default {DEFAULT_STEP_TIMEOUT}, 0 = no limit). Env: {STEP_TIMEOUT_ENV}"))
    parser.add_argument("--cleanup", action="store_true",
                        help=L("재생하지 않고, 남은 schema-timeline 라벨의 컨테이너·네트워크를 모두 지운다(돌고 있는 다른 재생도 지워진다)",
                               "Do not replay; remove all leftover containers and networks labelled schema-timeline (also removes other running replays)"))
    add_argument(parser)
    args = parser.parse_args()
    init(args)
    if args.cleanup:
        containers, networks = cleanup()
        print(L(f"컨테이너 {containers}개, 네트워크 {networks}개를 지웠습니다.",
                f"Removed {plural(containers, 'container', 'containers')} and {plural(networks, 'network', 'networks')}."))
        return
    if not args.migrations or not args.out:
        parser.error(L("migrations 와 --out 이 필요합니다", "migrations and --out are required"))
    set_step_timeout(args.step_timeout)
    install_signal_handlers()

    engine = engines.get(args.db)
    args.image = args.image or engine.default_image
    args.schema = args.schema or engine.default_schema
    src = load(args.format, args.migrations, args.file, engine.name)
    steps, skipped, warnings, notes = src.steps, src.skipped, list(src.warnings), list(src.notes)
    if not steps:
        raise SystemExit(L(f"{src.root} 에 재생할 마이그레이션이 없습니다", f"No migrations to replay in {src.root}"))
    if skipped and not args.ignore_repeatable:
        names = ", ".join(f.name for f in skipped)
        raise SystemExit(L(f"R__ 또는 SQL 콜백 파일이 있습니다: {names}\n이 파일들을 빼고 재생하려면 --ignore-repeatable 을 붙이세요.",
                           f"R__ or SQL callback files found: {names}\nAdd --ignore-repeatable to replay without them."))
    if src.prelude:
        warnings.append(L(f"{args.format} 기본 스키마의 대역({src.prelude.name})을 먼저 적용했습니다. 실제 플랫폼과 다를 수 있습니다.",
                          f"Applied a stand-in for the {args.format} default schema ({src.prelude.name}) first. It may differ from the real platform."))

    roles = referenced_roles(s["text"] if "text" in s else s["path"].read_text(encoding="utf-8-sig") for s in steps)
    roles = [r for r in roles if r not in src.platform_roles]
    for _ in range(10):
        snaps, rows, missing = replay_once(steps, args.image, roles if engine.has_roles else [], args.schema, src.prelude,
                                           src.skip_unavailable_extensions, engine)
        if missing is None:
            break
        roles.append(missing)
    else:
        raise SystemExit(L("없는 role 을 만들며 10번 다시 시도했지만 재생하지 못했습니다", "Retried 10 times creating missing roles but could not replay"))

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    for name in OUTPUTS:
        target = out / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    (out / "snapshots").mkdir()
    for i, doc in enumerate(snaps):
        (out / "snapshots" / f"{i:03d}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    meta = {"source": str(args.migrations.resolve().name), "input": str(src.root), "engine": engine.name, "image": args.image,
            "schema": args.schema,
            "format": args.format, "prelude": src.prelude.name if src.prelude else None,
            "roles_created": roles if engine.has_roles else [],
            "skipped_files": [f.name for f in skipped], "warnings": warnings, "notes": notes, "steps": rows}
    (out / "steps.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    failed = next((r for r in rows if r["status"] == "failed"), None)
    applied = sum(r["status"] == "applied" for r in rows)
    print(L(f"단계 {applied}/{len(steps)} 적용, 스냅샷 {len(snaps)}개 -> {out}", f"Applied {applied}/{len(steps)} {'step' if len(steps) == 1 else 'steps'}, {plural(len(snaps), 'snapshot', 'snapshots')} -> {out}"))
    if roles and engine.has_roles:
        print(L("  미리 만든 role: ", "  Roles created in advance: ") + ", ".join(roles))
    for n in notes:
        print(L("  안내: ", "  Note: ") + n)
    for w in warnings + [f"{r['file']}: {w}" for r in rows for w in r["warnings"]]:
        print(L("  경고: ", "  Warning: ") + w)
    if failed:
        print(L("  실패: ", "  Failed: ") + f"{failed['file']}\n{failed['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        raise SystemExit(L("실패: ", "Failed: ") + str(e))
