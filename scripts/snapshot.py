"""DDL 파일마다 빈 DB 를 만들어 적용하고, 카탈로그를 JSON 스냅샷으로 저장한다. PostgreSQL 용 컨테이너·psql 도우미도 여기 있다.

사용법: python3 snapshot.py v1.sql v2.sql --out out/snapshots [--db postgres|mysql|mariadb|oracle] [--image 이미지]
스냅샷 파일 이름은 입력 순서대로 00.json, 01.json ... 이다.
"""
import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from i18n import L, add_argument, early, init, plural

CATALOG_SQL = Path(__file__).with_name("catalog_postgres.sql").read_text()


def catalog_sql(schema="public"):
    """schema 를 대상으로 하는 카탈로그 조회 SQL. search_path 를 그 스키마로 두어 정의문이 스키마 접두어 없이 나오게 한다."""
    literal = "'" + schema.replace("'", "''") + "'"
    ident = '"' + schema.replace('"', '""') + '"'
    return f"SET search_path TO {ident}, public;\n" + CATALOG_SQL.replace("'public'", literal)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqltext import mask_snapshot  # noqa: E402


def run(cmd, stdin=None, check=True):
    """명령을 실행하고 stdout 을 돌려준다. 실패하면 stderr 를 담아 RuntimeError 를 던진다."""
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} ... " + L("실패", "failed") + f"\n{proc.stderr.strip()}")
    return proc.stdout


LABEL = "schema-timeline"
RUN_ID = os.environ.get("SCHEMA_TIMELINE_RUN") or uuid.uuid4().hex[:12]
LIMITS = {"memory": "1g", "cpus": "2", "pids": "512"}
LIMITS_ORACLE = {"memory": "3g", "cpus": "2", "pids": "2048"}
LIMIT_ENV = {"memory": "SCHEMA_TIMELINE_MEMORY", "cpus": "SCHEMA_TIMELINE_CPUS", "pids": "SCHEMA_TIMELINE_PIDS"}
STEP_TIMEOUT_ENV = "SCHEMA_TIMELINE_STEP_TIMEOUT"
DEFAULT_STEP_TIMEOUT = 300
_step_timeout = None
_tracking = False


class StepTimeout(RuntimeError):
    """SQL 적용이 제한 시간 안에 끝나지 않았다. seconds 는 적용한 제한(초)이다."""

    def __init__(self, seconds):
        super().__init__(f"timeout {seconds}s")
        self.seconds = seconds


def limits(kind=None):
    """컨테이너 자원 제한. 환경 변수(SCHEMA_TIMELINE_MEMORY·CPUS·PIDS)가 있으면 기본값을 덮는다. kind 가 oracle 이면 기본이 더 크다."""
    base = LIMITS_ORACLE if kind == "oracle" else LIMITS
    return {k: (os.environ.get(LIMIT_ENV[k]) or "").strip() or v for k, v in base.items()}


def run_argv(image, kind="postgres", network="none", rm=True, extra=()):
    """재생·비교용 컨테이너를 띄우는 docker run 명령. 자원 제한과 정리용 라벨(schema-timeline, schema-timeline.run)이 붙는다."""
    lim = limits(kind)
    return (["docker", "run", "-d"] + (["--rm"] if rm else []) + ["--network", network,
            "--memory", lim["memory"], "--memory-swap", lim["memory"], "--cpus", lim["cpus"], "--pids-limit", lim["pids"],
            "--label", f"{LABEL}=1", "--label", f"{LABEL}.run={RUN_ID}"] + list(extra) + [image])


def set_step_timeout(seconds):
    """단계 하나의 적용 제한 시간(초). None 이면 환경 변수, 없으면 기본값을 쓴다. 0 이하면 제한이 없다."""
    global _step_timeout
    _step_timeout = seconds


def step_timeout():
    """적용 제한 시간(초). 제한이 없으면 None."""
    value = _step_timeout
    if value is None:
        raw = (os.environ.get(STEP_TIMEOUT_ENV) or "").strip()
        try:
            value = float(raw) if raw else DEFAULT_STEP_TIMEOUT
        except ValueError:
            raise SystemExit(L(f"{STEP_TIMEOUT_ENV}={raw!r} 은 숫자(초)가 아닙니다", f"{STEP_TIMEOUT_ENV}={raw!r} is not a number of seconds"))
    return None if value <= 0 else value


def run_step(cmd, sql):
    """SQL 을 표준 입력으로 넣는 적용 명령을 제한 시간 안에서 실행한다.

    @throws StepTimeout 시간 안에 끝나지 않았을 때. 컨테이너 안의 프로세스는 컨테이너를 지울 때 함께 끝난다.
    """
    limit = step_timeout()
    try:
        return subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired:
        raise StepTimeout(int(limit) if limit == int(limit) else limit) from None


def timeout_text(what, seconds):
    """제한 시간 초과 문구. what 은 '단계 V7' 같은 대상 이름이다."""
    return L(f"{what} 이(가) {seconds}초 안에 끝나지 않았습니다(재생 중단). 오래 걸리는 문장이나 락 대기가 아닌지 확인하세요. "
             "제한은 --step-timeout 이나 SCHEMA_TIMELINE_STEP_TIMEOUT 으로 바꿉니다.",
             f"{what} did not finish within {seconds} s (replay stopped). Long-running statement or lock wait? "
             "Change the limit with --step-timeout or SCHEMA_TIMELINE_STEP_TIMEOUT.")


def remove_container(cid):
    """컨테이너를 강제로 지운다(익명 볼륨 포함). 이미 없어도 오류로 보지 않는다."""
    run(["docker", "rm", "--force", "--volumes", cid], check=False)


def cleanup(run_id=None):
    """라벨이 붙은 컨테이너와 네트워크를 지운다. run_id 가 있으면 그 실행의 것만, 없으면 모든 실행의 것을(라벨 없는 옛 네트워크 포함).

    @return (지운 컨테이너 수, 지운 네트워크 수)
    """
    flt = f"label={LABEL}" + (f".run={run_id}" if run_id else "")
    ids = run(["docker", "ps", "-a", "-q", "--filter", flt], check=False).split()
    if ids:
        run(["docker", "rm", "--force", "--volumes"] + ids, check=False)
    nets = run(["docker", "network", "ls", "-q", "--filter", flt], check=False).split()
    if not run_id:
        nets = sorted(set(nets) | set(run(["docker", "network", "ls", "-q", "--filter", f"name={LABEL}-"], check=False).split()))
    removed = 0
    for n in nets:
        for _ in range(10):
            if subprocess.run(["docker", "network", "rm", n], capture_output=True).returncode == 0:
                removed += 1
                break
            time.sleep(1)
    return len(ids), removed


def track():
    """이 실행이 띄운 컨테이너·네트워크를 프로세스가 끝날 때 한 번 더 지우도록 등록한다. docker run 보다 먼저 부른다."""
    global _tracking
    if not _tracking:
        _tracking = True
        atexit.register(cleanup, RUN_ID)


def install_signal_handlers():
    """SIGTERM·SIGHUP 을 SystemExit 로 바꿔 finally 와 atexit 정리가 돌게 한다."""
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda signum, frame: sys.exit(128 + signum))


def start_container(image):
    """네트워크 없는 임시 컨테이너를 띄우고 TCP 접속이 될 때까지 기다린다. 초기화용 임시 서버는 TCP 를 열지 않으므로 TCP 로 확인한다."""
    track()
    cid = run(run_argv(image, "postgres", extra=["-e", "POSTGRES_PASSWORD=snapshot"])).strip()
    try:
        for _ in range(60):
            ok = subprocess.run(
                ["docker", "exec", cid, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                capture_output=True,
            ).returncode == 0
            if ok:
                return cid
            time.sleep(1)
    except BaseException:
        remove_container(cid)
        raise
    remove_container(cid)
    raise RuntimeError(L("PostgreSQL 컨테이너가 60초 안에 준비되지 않았다", "PostgreSQL container was not ready within 60 seconds"))


def psql(cid, db, sql):
    """컨테이너 안의 psql 로 SQL 을 실행한다. 첫 오류에서 멈춘다."""
    return run(
        ["docker", "exec", "-i", cid, "psql", "-X", "-U", "postgres", "-d", db,
         "-v", "ON_ERROR_STOP=1", "-q", "-At", "-f", "-"],
        stdin=sql,
    )


def snapshot(cid, index, ddl_path, schema=None, engine=None, image=None):
    """DDL 하나를 새 DB 에 적용하고 그 DB 의 카탈로그 스냅샷을 돌려준다."""
    import engines
    engine = engine or engines.get("postgres")
    schema = schema or engine.default_schema
    db = engine.db_for(schema) if engine.name != "postgres" else f"s{index:02d}"
    if engine.name != "postgres" and index:
        engine.drop_db(cid, db, schema)
    engine.create_db(cid, db, schema)
    try:
        proc = engine.apply(cid, db, ddl_path.read_text(), single_tx=False, schema=schema)
    except StepTimeout as e:
        raise RuntimeError(timeout_text(str(ddl_path), e.seconds)) from None
    if proc.returncode != 0:
        raise RuntimeError(f"{ddl_path} " + L("적용 실패", "failed to apply") + f"\n{engine.error_text(proc.stderr)}")
    doc = engine.catalog(cid, db, schema)
    doc["engine"] = engine.name
    doc = engine.add_ddl(mask_snapshot(doc))
    doc["objects"] = engine.objects(cid, db, schema)
    doc["source"] = ddl_path.name
    doc["image"] = image
    return doc


def main():
    import engines
    early()
    parser = argparse.ArgumentParser()
    parser.add_argument("ddl", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--db", choices=engines.NAMES, default="postgres", help=L("DDL 을 적용할 DB 엔진", "Database engine to apply the DDL to"))
    parser.add_argument("--image", help=L("이미지(기본: 엔진마다 다름)", "Image (default depends on the engine)"))
    parser.add_argument("--schema", help=L("스냅샷을 뜰 스키마(엔진마다 기본값이 다름)", "Schema to snapshot (default depends on the engine)"))
    parser.add_argument("--step-timeout", type=float, metavar="SEC",
                        help=L(f"DDL 파일 하나를 적용하는 제한 시간(초, 기본 {DEFAULT_STEP_TIMEOUT}, 0 은 제한 없음). 환경 변수 {STEP_TIMEOUT_ENV}",
                               f"Time limit in seconds for applying one DDL file (default {DEFAULT_STEP_TIMEOUT}, 0 = no limit). Env: {STEP_TIMEOUT_ENV}"))
    add_argument(parser)
    args = parser.parse_args()
    init(args)
    set_step_timeout(args.step_timeout)
    install_signal_handlers()
    engine = engines.get(args.db)

    missing = [str(p) for p in args.ddl if not p.is_file()]
    if missing:
        print(L(f"DDL 파일이 없다: {', '.join(missing)}", f"DDL file not found: {', '.join(missing)}"), file=sys.stderr)
        sys.exit(2)
    args.out.mkdir(parents=True, exist_ok=True)
    cid = engine.start(args.image or engine.default_image)
    try:
        for i, path in enumerate(args.ddl):
            doc = snapshot(cid, i, path, args.schema, engine, args.image or engine.default_image)
            target = args.out / f"{i:02d}.json"
            target.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
            print(f"{path.name} -> {target} " + L(f"(테이블 {len(doc['tables'])}, 뷰 {len(doc['views'])})", f"({plural(len(doc['tables']), 'table', 'tables')}, {plural(len(doc['views']), 'view', 'views')})"))
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    finally:
        engine.stop(cid)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        raise SystemExit(L("실패: ", "Failed: ") + str(e))
