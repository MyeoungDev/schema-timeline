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
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from i18n import L
from snapshot import run

VERSIONED = re.compile(r"^V(\d+(?:[._]\d+)*)__(.+)\.sql$")
CALLBACKS = re.compile(r"^(before|after)\w*\.sql$", re.IGNORECASE)
NUMBERED = re.compile(r"^(\d+)_+(.+)\.sql$")
DOWN_FILE = re.compile(r"\.down\.sql$", re.IGNORECASE)
UP_SUFFIX = re.compile(r"\.up$", re.IGNORECASE)
PRELUDE_DIR = Path(__file__).resolve().parent / "preludes"
VERSION = r"\d+(?:[._]\d+)*"
UNDO = re.compile(rf"^U{VERSION}__.+\.sql$")
BASELINE = re.compile(rf"^B({VERSION})__(.+)\.sql$")
LOWER_PREFIX = re.compile(rf"^[vub]{VERSION}__.+\.sql$")
WRONG_SUFFIX = re.compile(rf"^[VUBvub]{VERSION}__.+\.sql$", re.IGNORECASE)
CANDIDATE = re.compile(r"^[A-Za-z]?\d.*\.sql$")
FLYWAY_PLACEHOLDER = re.compile(r"\$\{([\w.:-]+)\}")
FLYWAY_DEFAULTS = ("flyway:defaultSchema", "flyway:table", "flyway:filename", "flyway:timestamp")
TEMPLATE_TEXT = re.compile(r"\{\{[^{}\n]*\}\}")
LISTED_FILES = 8
SKIP_DIRS = {".git", "node_modules", "target", "build", "out", "dist", ".gradle", ".idea", "venv", ".venv", "__pycache__"}
NOT_HISTORY_DIRS = {"seed", "seeds", "fixtures", "testdata", "test", "tests"}


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


def walk_dirs(path):
    """path 와 그 아래 디렉터리를 이름순으로 (디렉터리, 파일 이름 목록) 으로 낸다. SKIP_DIRS 와 점으로 시작하는 디렉터리 안으로는 들어가지 않는다."""
    for top, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        yield Path(top), files


def is_prisma_dir(d, names):
    """Prisma 마이그레이션 디렉터리인지. migration_lock.toml 이 있거나, prisma/migrations 아래에 <이름>/migration.sql 이 있으면 그렇다."""
    if "migration_lock.toml" in names:
        return True
    return d.name == "migrations" and d.parent.name == "prisma" and any((c / "migration.sql").exists() for c in d.iterdir() if c.is_dir())


def numbered_sql_dirs(path):
    """번호 붙은 SQL 파일(.down.sql 제외)이 두 개 이상 바로 들어 있는 디렉터리. path 아래의 seed·fixture·test 디렉터리와 그 하위는 뺀다.

    @return {디렉터리: 파일 수}
    """
    out = {}
    for d, names in walk_dirs(path):
        if any(part.lower() in NOT_HISTORY_DIRS for part in d.relative_to(path).parts):
            continue
        n = sum(1 for x in names if NUMBERED.match(x) and not DOWN_FILE.search(x))
        if n >= 2:
            out[d] = n
    return out


def find_below(path, test, what):
    """path 아래에서 test 를 만족하는 디렉터리를 찾는다. 모노레포에서 packages/*/prisma 처럼 깊이 있는 이력을 찾을 때 쓴다.

    @param what 여러 곳일 때 안내에 넣을 이력 이름
    @return 한 곳이면 그 경로, 없으면 None
    @throws SystemExit 여러 곳이면 경로 목록과 함께 멈춘다
    """
    found = [d for d, _ in walk_dirs(path) if test(d)]
    if len(found) > 1:
        names = file_list([str(d.relative_to(path)) for d in found])
        raise SystemExit(L(f"{path} 아래 {what} 이력이 여러 곳입니다: {names}. 그중 하나의 경로를 지정하세요.",
                           f"Several {what} histories under {path}: {names}. Pass the path of one of them."))
    return found[0] if found else None


def file_list(names):
    """경고에 넣을 파일 이름 목록. 많으면 앞의 몇 개만 보이고 나머지는 개수로 센다."""
    shown = ", ".join(names[:LISTED_FILES])
    return shown + (L(f" 외 {len(names) - LISTED_FILES}개", f" and {len(names) - LISTED_FILES} more") if len(names) > LISTED_FILES else "")


def ignored_file_warnings(files, base):
    """Flyway 디렉터리에서 재생하지 않고 넘어가는 파일의 경고. undo(U), 소문자 접두사, .sql 이 아닌 대소문자 확장자,
    V<버전>__<설명>.sql 형식이 아닌 마이그레이션 후보.

    번호로 시작하는 후보는 V 파일이 있는 디렉터리 안에서만 본다. R__ 와 콜백, Java·Kotlin 파일은 다루지 않는다.
    @param files 파일 경로들, @param base 경고에 보일 이름의 기준 디렉터리
    @return 경고 목록
    """
    by_dir = {}
    for f in files:
        by_dir.setdefault(f.parent, []).append(f)
    found = {k: [] for k in ("undo", "lower", "suffix", "candidate")}
    for fs in by_dir.values():
        has_v = any(VERSIONED.match(f.name) for f in fs)
        for f in sorted(fs):
            n = f.name
            if VERSIONED.match(n) or BASELINE.match(n) or n.startswith("R__") or CALLBACKS.match(n):
                continue
            kind = ("undo" if UNDO.match(n) else "lower" if LOWER_PREFIX.match(n)
                    else "suffix" if f.suffix != ".sql" and WRONG_SUFFIX.match(n) else None)
            if not kind and f.suffix == ".sql" and (re.match(r"^[Vv]\d", n) or (has_v and CANDIDATE.match(n))):
                kind = "candidate"
            if kind:
                found[kind].append(str(f.relative_to(base)))
    out = []
    if found["undo"]:
        out.append(L(f"Flyway undo 마이그레이션(U<버전>__*.sql)은 재생하지 않습니다: {file_list(found['undo'])}",
                     f"Flyway undo migrations (U<version>__*.sql) are not replayed: {file_list(found['undo'])}"))
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


def find_steps(directory, recursive=True):
    """Flyway V 파일을 버전 순으로 정렬한다. baseline 파일(B<버전>__*.sql)이 있으면 가장 높은 버전의 B 를 첫 단계로 두고
    그 버전 이하의 V 파일과 나머지 B 파일은 빼며, 뺀 파일은 warnings 에 적는다.

    @return (steps, skipped, warnings). skipped 는 R__·SQL 콜백 파일, warnings 는 재생에 반영되지 않는 파일 안내다.
    """
    steps, skipped, warnings, baselines = [], [], [], []
    files = ([d / name for d, names in walk_dirs(directory) for name in names] if recursive
             else [directory / name for name in os.listdir(directory) if (directory / name).is_file()])
    for f in files:
        m = VERSIONED.match(f.name)
        if m:
            steps.append({"version": m.group(1).replace("_", "."), "key": version_key(m.group(1)),
                          "label": "V" + m.group(1).replace("_", "."),
                          "description": m.group(2).replace("_", " "), "path": f})
        elif BASELINE.match(f.name):
            b = BASELINE.match(f.name)
            baselines.append({"version": b.group(1).replace("_", "."), "key": version_key(b.group(1)),
                              "label": "B" + b.group(1).replace("_", "."), "description": b.group(2).replace("_", " "), "path": f})
        elif f.suffix == ".sql" and (f.name.startswith("R__") or CALLBACKS.match(f.name)):
            skipped.append(f)
        elif re.match(r"^V\d", f.name) and f.suffix.lower() in (".java", ".class", ".kt"):
            warnings.append(f"{f.name}: " + L("SQL 이 아닌 마이그레이션은 재생하지 않습니다", "Not replayed: not a SQL migration"))
    warnings += ignored_file_warnings(files, directory)
    steps.sort(key=lambda s: s["key"])
    no_duplicates(steps, ("버전", "version"))
    if baselines:
        no_duplicates(sorted(baselines, key=lambda b: b["key"]), ("버전", "version"))
        base = max(baselines, key=lambda b: b["key"])
        covered = [s["path"].name for s in steps if s["key"] <= base["key"]] + [b["path"].name for b in baselines if b is not base]
        steps = [base] + [s for s in steps if s["key"] > base["key"]]
        warnings.append(L(f"baseline {base['path'].name} 부터 재생합니다. 그 버전 이하라 재생하지 않은 파일: {file_list(covered)}" if covered
                          else f"baseline {base['path'].name} 부터 재생합니다.",
                          f"Replay starts from baseline {base['path'].name}. Not replayed because their version is not above it: {file_list(covered)}" if covered
                          else f"Replay starts from baseline {base['path'].name}."))
    return steps, skipped, warnings


def find_sql_files(directory):
    """번호를 앞에 붙인 SQL 파일(<번호>__설명.sql)을 번호 순으로 정렬한다. init 디렉터리나 init*.sql 은 맨 앞에 두고,
    <번호>_<설명>.up.sql / .down.sql 짝(golang-migrate)은 .up.sql 만 단계로 쓰며 .down.sql 은 경고에 모은다. SKIP_DIRS 와 점으로 시작하는 디렉터리는 보지 않는다.
    Liquibase formatted SQL 을 Liquibase 없이 파일 단위로 적용하는 이력에도 쓴다. --rollback 등 Liquibase 지시문은 주석이라 실행되지 않는다.
    @return (steps, skipped, warnings)
    """
    steps, warnings, downs = [], [], []
    files = sorted(d / n for d, names in walk_dirs(directory) for n in names if n.lower().endswith(".sql"))
    for f in files:
        rel = f.relative_to(directory)
        if DOWN_FILE.search(f.name):
            downs.append(str(rel))
            continue
        if rel.parts[0].lower().startswith("init") or f.stem.lower().startswith("init"):
            steps.append({"version": "0", "key": (0, str(rel)), "label": "init", "description": UP_SUFFIX.sub("", f.stem).replace("_", " "), "path": f})
            continue
        m = NUMBERED.match(f.name)
        if m:
            steps.append({"version": m.group(1), "key": (1, number_key(m.group(1))), "label": timestamp_label(m.group(1)),
                          "description": UP_SUFFIX.sub("", m.group(2)).replace("_", " "), "path": f})
        else:
            warnings.append(f"{rel}: " + L("이름이 <번호>__<설명>.sql 형식이 아니어서 재생하지 않습니다",
                                           "Not replayed: the name does not match <number>__<description>.sql"))
    if downs:
        warnings.append(L(f"되돌리기 파일(.down.sql)은 재생하지 않습니다: {file_list(downs)}",
                          f"Down migrations (.down.sql) are not replayed: {file_list(downs)}"))
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

    path 는 레포 루트, supabase/, supabase/migrations 중 무엇이든 된다. 그 자리에 없으면 아래 디렉터리에서 supabase/migrations 를 찾는다. seed.sql 은 데이터라 재생하지 않는다.
    @return (steps, skipped, warnings, root)
    """
    has = lambda d: any(SUPABASE_FILE.match(f.name) for f in d.glob("*.sql"))
    root = first_dir(path, ["migrations", "supabase/migrations"], has) \
        or find_below(path, lambda d: d.name == "migrations" and d.parent.name == "supabase" and has(d), "Supabase")
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


PRISMA_DIR = re.compile(r"^(\d+)_(.*)$")


def prisma_provider(root):
    """migration_lock.toml 의 provider. 파일이 없으면 None."""
    lock = root / "migration_lock.toml"
    if not lock.exists():
        return None
    m = re.search(r'^\s*provider\s*=\s*"([^"]+)"', lock.read_text(errors="ignore"), re.M)
    return m.group(1) if m else None


def find_prisma(path, engine="postgres"):
    """Prisma Migrate 이력(prisma/migrations/<타임스탬프>_<이름>/migration.sql)을 디렉터리 이름 사전순으로. baseline(0_init)도 포함한다.

    path 는 레포 루트, prisma/, prisma/migrations 중 무엇이든 된다. 그 자리에 없으면 아래 디렉터리에서 migration_lock.toml 이나 prisma/migrations 를 찾는다.
    이름 없이 만든 마이그레이션(<타임스탬프>_)도 단계로 쓰고 설명에는 디렉터리 이름을 넣는다. provider 가 재생할 엔진과 다르면 멈춘다.
    @return (steps, skipped, warnings, root)
    """
    has = lambda d: (d / "migration_lock.toml").exists() or any((c / "migration.sql").exists() for c in d.iterdir() if c.is_dir())
    root = first_dir(path, ["migrations", "prisma/migrations"], has) \
        or find_below(path, lambda d: is_prisma_dir(d, {c.name for c in d.iterdir() if c.is_file()}), "Prisma")
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
                          "description": m.group(2).replace("_", " ") or d.name, "path": sql, "file": f"{d.name}/migration.sql"})
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
    path 는 레포 루트나 out 디렉터리(기본 drizzle/). 그 자리에 없으면 아래 디렉터리에서 meta/_journal.json 을 찾는다. dialect 가 재생할 엔진과 다르면 멈춘다.
    @return (steps, skipped, warnings, root)
    """
    candidates = ["drizzle", "migrations", "src/drizzle", "db/migrations", "src/db/migrations"]
    journal = lambda d: (d / "meta" / "_journal.json").exists()
    root = first_dir(path, candidates, journal) or find_below(path, journal, "Drizzle")
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


def guess_format(path):
    """--format 이 없을 때 path 아래 이력의 형식과 재생할 디렉터리를 고른다.

    Flyway V 파일·Prisma·Drizzle(meta/_journal.json)·Supabase(supabase/migrations) 중 한 형식만 있으면 그 형식과 path,
    하나도 없으면 번호 붙은 SQL 파일이 모인 디렉터리(numbered_sql_dirs)가 한 곳이거나 모두 한 디렉터리 아래에 있을 때 sqlfiles 와 그 최상위 디렉터리다.
    @return (형식, 디렉터리). 아무것도 없으면 (None, path)
    @throws SystemExit 여러 형식이 섞여 있거나 번호 붙은 SQL 디렉터리가 여러 곳이면 목록과 함께 멈춘다
    """
    path = Path(path)
    base = path.resolve()
    if base.is_file():
        return None, path
    kinds = set()
    flyway_dirs = []
    for d, names in walk_dirs(base):
        if any(VERSIONED.match(n) or BASELINE.match(n) for n in names):
            kinds.add("flyway")
            flyway_dirs.append(d)
        if is_prisma_dir(d, names):
            kinds.add("prisma")
        if d.name == "meta" and "_journal.json" in names:
            kinds.add("drizzle")
        if d.name == "migrations" and d.parent.name == "supabase" and any(SUPABASE_FILE.match(n) for n in names):
            kinds.add("supabase")
    if len(kinds) > 1:
        raise SystemExit(L(f"{path} 에 여러 형식의 이력이 있습니다({', '.join(sorted(kinds))}). --format 과 그 이력의 디렉터리를 주세요.",
                           f"{path} contains histories in several formats ({', '.join(sorted(kinds))}). Pass --format and that history's directory."))
    if kinds:
        if "flyway" in kinds:
            if len(flyway_dirs) > 1:
                names = file_list([str(d.relative_to(base)) or "." for d in flyway_dirs])
                raise SystemExit(L(f"{path} 아래 Flyway 이력이 여러 곳입니다: {names}. 그중 하나의 경로를 지정하세요.",
                                   f"Several Flyway histories under {path}: {names}. Pass the path of one of them."))
            return "flyway", flyway_dirs[0]
        return kinds.pop(), path
    dirs = numbered_sql_dirs(base)
    top = min(dirs, key=lambda d: len(d.parts), default=None)
    if top and all(d == top or top in d.parents for d in dirs):
        return "sqlfiles", top
    if len(dirs) > 1:
        names = file_list([str(d.relative_to(base)) or "." for d in dirs])
        raise SystemExit(L(f"{path} 아래 번호 붙은 SQL 파일 디렉터리가 여러 곳입니다: {names}. --format sqlfiles 와 함께 그중 하나의 경로를 주세요.",
                           f"Several directories of numbered SQL files under {path}: {names}. Pass --format sqlfiles with the path of one of them."))
    if dirs:
        return "sqlfiles", next(iter(dirs))
    return None, path


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
        versions = [s["version"] for s in src.steps if not BASELINE.match(s["path"].name)]
        return src._replace(notes=version_gaps(versions) + timestamp_order_notes(versions))
    raise SystemExit(L(f"모르는 형식: {fmt} (지원: {', '.join(FORMATS)})", f"Unknown format: {fmt} (supported: {', '.join(FORMATS)})"))
