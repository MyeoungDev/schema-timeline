"""마이그레이션 도구별 입력 어댑터. 레포·디렉터리에서 적용 순서대로 정렬한 SQL 단계 목록을 만든다.

단계(step) 하나는 dict 다.
  version      정렬·표시용 버전 문자열
  key          정렬 키
  label        버전 막대에 쓰는 짧은 이름(V3, 24.05.01, #0003 등)
  description  설명
  path | text  SQL 파일 경로, 또는 SQL 본문(git 리비전처럼 파일이 없을 때)
  file         화면에 보일 파일 이름(없으면 path 의 이름)
  full         True 면 이 단계는 빈 DB 에 처음부터 적용한다(git-snapshots)

load(format, path, file) 은 Source 를 돌려준다. 재생(replay.py)은 형식을 모르고 이 목록만 쓴다.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from i18n import L
from snapshot import run

VERSIONED = re.compile(r"^V(\d+(?:[._]\d+)*)__(.+)\.sql$")
CALLBACKS = re.compile(r"^(before|after)\w*\.sql$", re.IGNORECASE)
NUMBERED = re.compile(r"^(\d{3,})_+(.+)\.sql$")
PRELUDE_DIR = Path(__file__).resolve().parent / "preludes"
VERSION = r"\d+(?:[._]\d+)*"
UNDO = re.compile(rf"^U{VERSION}__.+\.sql$")
BASELINE = re.compile(rf"^B{VERSION}__.+\.sql$")
LOWER_PREFIX = re.compile(rf"^[vub]{VERSION}__.+\.sql$")
WRONG_SUFFIX = re.compile(rf"^[VUBvub]{VERSION}__.+\.sql$", re.IGNORECASE)
CANDIDATE = re.compile(r"^[A-Za-z]?\d.*\.sql$")
LISTED_FILES = 8


class Source(NamedTuple):
    """어댑터 결과.

    steps     적용 순서대로 정렬한 단계
    skipped   기본으로는 재생하지 않는 파일(R__, SQL 콜백). 있으면 --ignore-repeatable 이 필요하다
    warnings  재생에 반영되지 않는 파일 등 안내
    root      실제로 읽은 디렉터리(레포 루트를 받았으면 그 안의 마이그레이션 디렉터리)
    prelude   첫 단계 전에 적용할 SQL 파일(플랫폼 기본 스키마의 대역). 없으면 None
    skip_unavailable_extensions  True 면 이미지에 없는 확장의 CREATE EXTENSION 을 건너뛰고 경고한다
    platform_roles  prelude 가 만드는 역할. "이력이 참조하지만 만들지 않은 역할"에서 뺀다
    notes     재생에 영향은 없지만 알아둘 점(버전 공백, 타임스탬프 순서)
    """
    steps: list
    skipped: list
    warnings: list
    root: Path
    prelude: Path = None
    skip_unavailable_extensions: bool = False
    platform_roles: tuple = ()
    notes: tuple = ()


def version_key(v):
    """Flyway 버전 비교 키. 끝의 0 은 같은 버전으로 본다(1 == 1.0)."""
    parts = [int(x) for x in re.split(r"[._]", v)]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def timestamp_label(v):
    """yyyyMMddHHmm... 형식이면 yy.MM.dd, 아니면 #번호."""
    return f"{v[2:4]}.{v[4:6]}.{v[6:8]}" if len(v) >= 8 and v[:2] in ("19", "20") else f"#{v}"


def number_key(v):
    """파일 번호 비교 키. yyyyMMdd... 타임스탬프는 자릿수가 달라도 시각 순이 되도록 오른쪽을 0으로 채운다."""
    if len(v) >= 8 and v[:2] in ("19", "20"):
        return v.ljust(20, "0")
    return v.zfill(20)


def no_duplicates(steps, what):
    """정렬 키가 같은 단계가 있으면 멈춘다."""
    seen = {}
    for s in steps:
        if s["key"] in seen:
            raise SystemExit(L(f"{what[0]}이 겹칩니다: ", f"Duplicate {what[1]}: ") + f"{seen[s['key']]}, {s.get('file') or s['path'].name}")
        seen[s["key"]] = s.get("file") or s["path"].name


def first_dir(path, candidates, test):
    """path 와 그 아래 후보 경로 중 test 를 만족하는 첫 디렉터리. 없으면 None."""
    for c in [path] + [path / c for c in candidates]:
        if c.is_dir() and test(c):
            return c
    return None


def file_list(names):
    """경고에 넣을 파일 이름 목록. 많으면 앞의 몇 개만 보이고 나머지는 개수로 센다."""
    shown = ", ".join(names[:LISTED_FILES])
    return shown + (L(f" 외 {len(names) - LISTED_FILES}개", f" and {len(names) - LISTED_FILES} more") if len(names) > LISTED_FILES else "")


def ignored_file_warnings(files, base):
    """Flyway 디렉터리에서 재생하지 않고 넘어가는 파일의 경고. undo(U), baseline(B), 소문자 접두사, .sql 이 아닌 대소문자 확장자,
    V<버전>__<설명>.sql 형식이 아닌 마이그레이션 후보.

    번호로 시작하는 후보는 V 파일이 있는 디렉터리 안에서만 본다. R__ 와 콜백, Java·Kotlin 파일은 다루지 않는다.
    @param files 파일 경로들, @param base 경고에 보일 이름의 기준 디렉터리
    @return 경고 목록
    """
    by_dir = {}
    for f in files:
        by_dir.setdefault(f.parent, []).append(f)
    found = {k: [] for k in ("undo", "baseline", "lower", "suffix", "candidate")}
    for fs in by_dir.values():
        has_v = any(VERSIONED.match(f.name) for f in fs)
        for f in sorted(fs):
            n = f.name
            if VERSIONED.match(n) or n.startswith("R__") or CALLBACKS.match(n):
                continue
            kind = ("undo" if UNDO.match(n) else "baseline" if BASELINE.match(n) else "lower" if LOWER_PREFIX.match(n)
                    else "suffix" if f.suffix != ".sql" and WRONG_SUFFIX.match(n) else None)
            if not kind and f.suffix == ".sql" and (re.match(r"^[Vv]\d", n) or (has_v and CANDIDATE.match(n))):
                kind = "candidate"
            if kind:
                found[kind].append(str(f.relative_to(base)))
    out = []
    if found["undo"]:
        out.append(L(f"Flyway undo 마이그레이션(U<버전>__*.sql)은 재생하지 않습니다: {file_list(found['undo'])}",
                     f"Flyway undo migrations (U<version>__*.sql) are not replayed: {file_list(found['undo'])}"))
    if found["baseline"]:
        out.append(L(f"baseline 마이그레이션(B<버전>__*.sql)은 재생하지 않습니다. 재생은 빈 DB 에서 V 파일부터 시작합니다: {file_list(found['baseline'])}",
                     f"Baseline migrations (B<version>__*.sql) are not replayed. Replay starts from an empty database with the V files: {file_list(found['baseline'])}"))
    if found["lower"]:
        out.append(L(f"접두사가 소문자라 마이그레이션으로 인식하지 않고 재생하지 않습니다(V·U·B 는 대문자): {file_list(found['lower'])}",
                     f"Not replayed: the prefix is lowercase and the file is not recognised as a migration (V, U and B must be uppercase): {file_list(found['lower'])}"))
    if found["suffix"]:
        out.append(L(f"확장자가 .sql 이 아니어서(대소문자 구분) 재생하지 않습니다: {file_list(found['suffix'])}",
                     f"Not replayed: the extension is not exactly .sql (case-sensitive): {file_list(found['suffix'])}"))
    if found["candidate"]:
        out.append(L(f"마이그레이션처럼 보이지만 V<버전>__<설명>.sql 형식이 아니어서 재생하지 않습니다: {file_list(found['candidate'])}",
                     f"Look like migrations but do not match V<version>__<description>.sql, so they are not replayed: {file_list(found['candidate'])}"))
    return out


def version_gaps(versions, prefix="V", width=0):
    """정렬된 정수 버전 사이에 빠진 번호를 안내한다. 점이 있는 버전, 타임스탬프(8자리 이상)는 보지 않고, 둘 사이에 다른 버전이 끼어 있어도 이웃으로 보지 않는다.

    @param versions 버전 문자열들, @param prefix 표시 접두사(V, #), @param width 번호를 0 으로 채울 자릿수
    @return 안내 목록
    """
    ordered = sorted(versions, key=version_key)
    plain = lambda v: re.fullmatch(r"\d+", v) and len(v) < 8
    fmt = lambda n: f"{prefix}{str(n).zfill(width)}"
    out = []
    for a, b in zip(ordered, ordered[1:]):
        if plain(a) and plain(b) and int(b) - int(a) > 1:
            lo, hi = int(a) + 1, int(b) - 1
            missing = fmt(lo) if lo == hi else f"{fmt(lo)}-{fmt(hi)}"
            out.append(L(f"{missing} 이(가) {fmt(int(a))} 와 {fmt(int(b))} 사이에 없습니다(버전 공백).",
                         f"{missing} {'is' if lo == hi else 'are'} missing between {fmt(int(a))} and {fmt(int(b))} (version gap)."))
    return out


def timestamp_order_notes(versions):
    """타임스탬프 버전의 자릿수가 섞여 숫자 순서가 시각 순서와 다르면 안내한다(202405011200 은 20240502 보다 숫자로 크다).

    @return 안내 목록
    """
    ts = [v for v in versions if re.fullmatch(r"\d{8,}", v) and v[:2] in ("19", "20")]
    by_number = sorted(ts, key=int)
    by_time = sorted(ts, key=lambda v: v.ljust(20, "0"))
    if by_number == by_time:
        return []
    return [L("타임스탬프 버전의 자릿수가 섞여 있어 숫자 순서와 시각 순서가 다릅니다. 재생은 숫자 순서로 적용합니다: " + ", ".join(by_number[:LISTED_FILES]),
              "Timestamp versions have mixed lengths, so numeric order differs from chronological order. Replay applies them in numeric order: " + ", ".join(by_number[:LISTED_FILES]))]


def find_steps(directory):
    """Flyway V 파일을 버전 순으로 정렬한다.

    @return (steps, skipped, warnings). skipped 는 R__·SQL 콜백 파일, warnings 는 재생에 반영되지 않는 파일 안내다.
    """
    steps, skipped, warnings = [], [], []
    files = [f for f in sorted(directory.rglob("*")) if f.is_file()]
    for f in files:
        m = VERSIONED.match(f.name)
        if m:
            steps.append({"version": m.group(1).replace("_", "."), "key": version_key(m.group(1)),
                          "label": "V" + m.group(1).replace("_", "."),
                          "description": m.group(2).replace("_", " "), "path": f})
        elif f.suffix == ".sql" and (f.name.startswith("R__") or CALLBACKS.match(f.name)):
            skipped.append(f)
        elif re.match(r"^V\d", f.name) and f.suffix.lower() in (".java", ".class", ".kt"):
            warnings.append(f"{f.name}: " + L("SQL 이 아닌 마이그레이션은 재생하지 않습니다", "Not replayed: not a SQL migration"))
    warnings += ignored_file_warnings(files, directory)
    steps.sort(key=lambda s: s["key"])
    no_duplicates(steps, ("버전", "version"))
    return steps, skipped, warnings


def find_sql_files(directory):
    """번호를 앞에 붙인 SQL 파일(<번호>__설명.sql)을 번호 순으로 정렬한다. init 디렉터리나 init*.sql 은 맨 앞에 둔다.

    Liquibase formatted SQL 을 Liquibase 없이 파일 단위로 적용하는 이력에 쓴다. --rollback 등 Liquibase 지시문은 주석이라 실행되지 않는다.
    @return (steps, skipped, warnings)
    """
    steps, warnings = [], []
    for f in sorted(directory.rglob("*.sql")):
        rel = f.relative_to(directory)
        if rel.parts[0].lower().startswith("init") or f.stem.lower().startswith("init"):
            steps.append({"version": "0", "key": (0, str(rel)), "label": "init", "description": f.stem.replace("_", " "), "path": f})
            continue
        m = NUMBERED.match(f.name)
        if m:
            steps.append({"version": m.group(1), "key": (1, number_key(m.group(1))), "label": timestamp_label(m.group(1)),
                          "description": m.group(2).replace("_", " "), "path": f})
        else:
            warnings.append(f"{rel}: " + L("이름이 <번호>__<설명>.sql 형식이 아니어서 재생하지 않습니다",
                                           "Not replayed: the name does not match <number>__<description>.sql"))
    steps.sort(key=lambda s: s["key"])
    no_duplicates(steps, ("번호", "number"))
    return steps, [], warnings


def find_git_snapshots(repo, rel):
    """전체 스키마 파일 하나(rel)의 git 리비전을 오래된 것부터. 각 리비전이 그 시점의 완성된 스키마다.

    단계마다 빈 DB 에 파일 전체를 적용하므로 oid 가 이어지지 않는다. 이름 변경은 '의심'으로만 나온다.
    @return (steps, skipped, warnings)
    """
    log = run(["git", "-C", str(repo), "log", "--reverse", "--format=%H%x09%ad%x09%s", "--date=short", "--", rel])
    steps = []
    for k, line in enumerate(l for l in log.splitlines() if l.strip()):
        sha, date, subject = line.split("\t", 2)
        text = run(["git", "-C", str(repo), "show", f"{sha}:{rel}"], check=False)
        steps.append({"version": sha[:8], "key": (k,), "label": f"{date[2:4]}.{date[5:7]}.{date[8:10]}",
                      "description": subject, "file": f"{Path(rel).name}@{sha[:8]}", "text": text, "full": True})
    if not steps:
        raise SystemExit(L(f"{repo} 에서 {rel} 의 git 이력을 찾지 못했습니다", f"No git history found for {rel} in {repo}"))
    return steps, [], []


SUPABASE_FILE = re.compile(r"^(\d+)_(.+)\.sql$")
SUPABASE_ROLES = ("anon", "authenticated", "service_role", "authenticator", "supabase_admin", "supabase_auth_admin",
                  "supabase_storage_admin", "supabase_realtime_admin", "dashboard_user")


def find_supabase(path):
    """Supabase CLI 이력(supabase/migrations/<타임스탬프>_<이름>.sql)을 타임스탬프 순으로.

    path 는 레포 루트, supabase/, supabase/migrations 중 무엇이든 된다. seed.sql 은 데이터라 재생하지 않는다.
    @return (steps, skipped, warnings, root)
    """
    has = lambda d: any(SUPABASE_FILE.match(f.name) for f in d.glob("*.sql"))
    root = first_dir(path, ["migrations", "supabase/migrations"], has)
    if not root:
        raise SystemExit(L(f"{path} 에서 supabase/migrations/<타임스탬프>_<이름>.sql 을 찾지 못했습니다",
                           f"No supabase/migrations/<timestamp>_<name>.sql found in {path}"))
    steps, warnings = [], []
    for f in sorted(root.glob("*.sql")):
        m = SUPABASE_FILE.match(f.name)
        if m:
            steps.append({"version": m.group(1), "key": (number_key(m.group(1)), m.group(1)), "label": timestamp_label(m.group(1)),
                          "description": m.group(2).replace("_", " "), "path": f})
        else:
            warnings.append(f"{f.name}: " + L("이름이 <타임스탬프>_<이름>.sql 형식이 아니어서 재생하지 않습니다",
                                              "Not replayed: the name does not match <timestamp>_<name>.sql"))
    steps.sort(key=lambda s: s["key"])
    no_duplicates(steps, ("타임스탬프", "timestamp"))
    return steps, [], warnings, root


PRISMA_DIR = re.compile(r"^(\d+)_(.+)$")


def prisma_provider(root):
    """migration_lock.toml 의 provider. 파일이 없으면 None."""
    lock = root / "migration_lock.toml"
    if not lock.exists():
        return None
    m = re.search(r'^\s*provider\s*=\s*"([^"]+)"', lock.read_text(errors="ignore"), re.M)
    return m.group(1) if m else None


def find_prisma(path, engine="postgres"):
    """Prisma Migrate 이력(prisma/migrations/<타임스탬프>_<이름>/migration.sql)을 디렉터리 이름 사전순으로. baseline(0_init)도 포함한다.

    path 는 레포 루트, prisma/, prisma/migrations 중 무엇이든 된다. provider 가 재생할 엔진과 다르면 멈춘다.
    @return (steps, skipped, warnings, root)
    """
    has = lambda d: (d / "migration_lock.toml").exists() or any((c / "migration.sql").exists() for c in d.iterdir() if c.is_dir())
    root = first_dir(path, ["migrations", "prisma/migrations"], has)
    if not root:
        raise SystemExit(L(f"{path} 에서 prisma/migrations/<타임스탬프>_<이름>/migration.sql 을 찾지 못했습니다",
                           f"No prisma/migrations/<timestamp>_<name>/migration.sql found in {path}"))
    provider = prisma_provider(root)
    if provider and engine_family(engine_of(provider)) != engine_family(engine):
        raise SystemExit(L(f"Prisma provider 가 {provider} 입니다. --db {engine_of(provider) or provider} 로 재생하세요"
                           + ("" if engine_of(provider) else "(지원하지 않는 DB)") + ".",
                           f"The Prisma provider is {provider}. Replay with --db {engine_of(provider) or provider}"
                           + ("" if engine_of(provider) else " (unsupported database)") + "."))
    steps, warnings = [], []
    if not provider:
        warnings.append(L(f"migration_lock.toml 이 없어 provider 를 확인하지 못했습니다. --db {engine} 로 보고 재생합니다.",
                          f"migration_lock.toml is missing, so the provider is unknown. Replaying as --db {engine}."))
    for d in sorted(c for c in root.iterdir() if c.is_dir()):
        m = PRISMA_DIR.match(d.name)
        sql = d / "migration.sql"
        if m and sql.exists():
            steps.append({"version": m.group(1), "key": d.name, "label": timestamp_label(m.group(1)),
                          "description": m.group(2).replace("_", " "), "path": sql, "file": f"{d.name}/migration.sql"})
        else:
            warnings.append(f"{d.name}: " + L("<타임스탬프>_<이름>/migration.sql 형식이 아니어서 재생하지 않습니다",
                                              "Not replayed: does not match <timestamp>_<name>/migration.sql"))
    steps.sort(key=lambda s: s["key"])
    no_duplicates(steps, ("타임스탬프", "timestamp"))
    return steps, [], warnings, root


DRIZZLE_TAG = re.compile(r"^(\d+)_(.+)$")


def drizzle_journal(root):
    """meta/_journal.json 을 읽는다. 깨졌으면 멈춘다."""
    try:
        journal = json.loads((root / "meta" / "_journal.json").read_text())
        entries = sorted(journal.get("entries", []), key=lambda e: e.get("idx", 0))
        if any("tag" not in e for e in entries):
            raise ValueError(L("tag 가 없는 항목", "entry without a tag"))
    except (ValueError, AttributeError, TypeError) as e:
        raise SystemExit(L(f"{root / 'meta' / '_journal.json'} 을 읽지 못했습니다: {e}", f"Cannot read {root / 'meta' / '_journal.json'}: {e}"))
    return str(journal.get("dialect", "")).lower(), entries


def find_drizzle(path, engine="postgres"):
    """Drizzle Kit 이력을 적용 순서대로.

    meta/_journal.json 이 있으면 그 entries 순서대로 <tag>.sql 을 쓴다. journal 에 없는 .sql 은 Drizzle 도 적용하지 않으므로 재생하지 않는다.
    journal 이 없고 <이름>/migration.sql 디렉터리들이 있으면 디렉터리 이름 순으로 쓴다.
    path 는 레포 루트나 out 디렉터리(기본 drizzle/). dialect 가 재생할 엔진과 다르면 멈춘다.
    @return (steps, skipped, warnings, root)
    """
    candidates = ["drizzle", "migrations", "src/drizzle", "db/migrations", "src/db/migrations"]
    root = first_dir(path, candidates, lambda d: (d / "meta" / "_journal.json").exists())
    if root:
        dialect, entries = drizzle_journal(root)
        if dialect and engine_family(engine_of(dialect)) != engine_family(engine):
            raise SystemExit(L(f"Drizzle dialect 가 {dialect} 입니다. --db {engine_of(dialect) or dialect} 로 재생하세요"
                               + ("" if engine_of(dialect) else "(지원하지 않는 DB)") + ".",
                               f"The Drizzle dialect is {dialect}. Replay with --db {engine_of(dialect) or dialect}"
                               + ("" if engine_of(dialect) else " (unsupported database)") + "."))
        steps, warnings = [], []
        for e in entries:
            tag = e["tag"]
            f = root / f"{tag}.sql"
            if not f.exists():
                raise SystemExit(L(f"journal 에 있는 {tag}.sql 이 없습니다", f"{tag}.sql listed in the journal does not exist"))
            m = DRIZZLE_TAG.match(tag)
            when = e.get("when")
            label = (datetime.fromtimestamp(when / 1000, timezone.utc).strftime("%y.%m.%d") if isinstance(when, (int, float))
                     else f"#{m.group(1) if m else e.get('idx')}")
            steps.append({"version": m.group(1) if m else str(e.get("idx")), "key": (e.get("idx", 0), tag), "label": label,
                          "description": (m.group(2) if m else tag).replace("_", " "), "path": f})
        listed = {s["path"].name for s in steps}
        warnings += [f"{f.name}: " + L("journal 에 없어 재생하지 않습니다", "Not replayed: not listed in the journal") for f in sorted(root.glob("*.sql")) if f.name not in listed]
        no_duplicates(steps, ("순번", "index"))
        return steps, [], warnings, root
    folders = lambda d: any((c / "migration.sql").exists() for c in d.iterdir() if c.is_dir())
    root = first_dir(path, candidates, folders)
    if not root:
        raise SystemExit(L(f"{path} 에서 Drizzle 의 meta/_journal.json 이나 <이름>/migration.sql 을 찾지 못했습니다"
                           "(drizzle.config 의 out 디렉터리를 지정하세요)",
                           f"No Drizzle meta/_journal.json or <name>/migration.sql found in {path} "
                           "(point to the out directory from drizzle.config)"))
    steps = []
    for d in sorted(c for c in root.iterdir() if c.is_dir() and (c / "migration.sql").exists()):
        m = DRIZZLE_TAG.match(d.name)
        v = m.group(1) if m else d.name
        steps.append({"version": v, "key": d.name, "label": timestamp_label(v) if m else d.name[:10],
                      "description": (m.group(2) if m else d.name).replace("_", " "), "path": d / "migration.sql",
                      "file": f"{d.name}/migration.sql"})
    return steps, [], [L("journal 이 없어 디렉터리 이름 순으로 적용합니다. dialect 는 확인하지 못했습니다.",
                         "No journal found, so folders are applied in name order. The dialect is unknown.")], root


def engine_of(name):
    """도구 설정의 DB 이름(provider, dialect)을 엔진 이름으로. 모르는 이름이면 None."""
    n = (name or "").lower()
    if n in ("postgresql", "postgres", "pg"):
        return "postgres"
    if n in ("mysql", "mariadb", "oracle"):
        return n
    return None


def engine_family(engine):
    """MySQL 과 MariaDB 는 같은 계열로 본다."""
    return "mysql" if engine in ("mysql", "mariadb") else engine


FORMATS = ("flyway", "sqlfiles", "git-snapshots", "supabase", "prisma", "drizzle")


def load(fmt, path, file=None, engine="postgres"):
    """형식에 맞는 어댑터로 단계 목록을 만든다.

    @param fmt FORMATS 중 하나
    @param path 레포 루트나 마이그레이션 디렉터리
    @param file git-snapshots 에서 쓸 전체 스키마 파일의 레포 기준 경로
    @param engine 재생할 DB 엔진 이름. Supabase 는 PostgreSQL 만 된다
    @return Source
    """
    path = Path(path)
    if fmt == "git-snapshots":
        if not file:
            raise SystemExit(L("git-snapshots 는 --file <레포 기준 경로> 가 필요합니다", "git-snapshots needs --file <path relative to the repo>"))
        return Source(*find_git_snapshots(path, file), root=path)
    if fmt == "supabase":
        if engine != "postgres":
            raise SystemExit(L("Supabase 이력은 PostgreSQL 로만 재생합니다(--db postgres).", "Supabase history can only be replayed on PostgreSQL (--db postgres)."))
        return Source(*find_supabase(path), prelude=PRELUDE_DIR / "supabase.sql", skip_unavailable_extensions=True,
                      platform_roles=SUPABASE_ROLES)
    if fmt == "prisma":
        return Source(*find_prisma(path, engine))
    if fmt == "drizzle":
        return Source(*find_drizzle(path, engine))
    if fmt == "sqlfiles":
        src = Source(*find_sql_files(path), root=path)
        numbered = [s for s in src.steps if s["label"] != "init"]
        width = len(numbered[0]["version"]) if numbered else 0
        return src._replace(notes=version_gaps([s["version"] for s in numbered], "#", width))
    if fmt == "flyway":
        src = Source(*find_steps(path), root=path)
        versions = [s["version"] for s in src.steps]
        return src._replace(notes=version_gaps(versions) + timestamp_order_notes(versions))
    raise SystemExit(L(f"모르는 형식: {fmt} (지원: {', '.join(FORMATS)})", f"Unknown format: {fmt} (supported: {', '.join(FORMATS)})"))
