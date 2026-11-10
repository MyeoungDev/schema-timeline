"""레포(또는 마이그레이션 디렉터리)를 읽어 마이그레이션 형식, DB 종류, 단계 수, 확인할 점을 JSON 으로 낸다.

알아보는 형식: Flyway, Supabase CLI, Prisma Migrate, Drizzle Kit, 번호 붙은 SQL 파일. Alembic·Liquibase changelog 는 알아보기만 한다.

사용법: python3 detect.py <레포 또는 디렉터리> [--lang ko|en]
경고 문구는 --lang 의 언어로 낸다. JSON 키와 값은 언어와 무관하다.

설정 파일에서는 DB 종류, locations, placeholder 이름만 읽는다. 접속 URL·계정·비밀번호는 결과에 넣지 않는다.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i18n import L, add_argument, early, init as init_lang  # noqa: E402
from sources import ignored_file_warnings, timestamp_order_notes, version_gaps  # noqa: E402

SKIP_DIRS = {".git", "node_modules", "target", "build", "out", "dist", ".gradle", ".idea", "venv", ".venv", "__pycache__"}
VERSIONED = re.compile(r"^V\d+(?:[._]\d+)*__.+\.sql$")
CALLBACK = re.compile(r"^(before|after)\w*\.sql$", re.IGNORECASE)
DB_FROM_URL = [(r"jdbc:postgresql|postgres(?:ql)?://", "postgresql"), (r"jdbc:mysql|mysql://", "mysql"),
               (r"jdbc:mariadb|mariadb://", "mariadb"), (r"jdbc:sqlserver", "sqlserver"), (r"jdbc:oracle", "oracle"),
               (r"jdbc:h2", "h2")]
ENGINES = {"postgresql": "postgres", "mysql": "mysql", "mariadb": "mariadb", "oracle": "oracle"}
PLACEHOLDER = re.compile(r"\$\{([^}:]+)(?::[^}]*)?\}")


def walk(root):
    for p in root.rglob("*"):
        parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in parts[:-1]):
            continue
        if p.is_file():
            yield p


def db_type(text):
    for pat, name in DB_FROM_URL:
        if re.search(pat, text, re.IGNORECASE):
            return name
    return None


def read_configs(files, root):
    """Flyway·Spring 설정에서 DB 종류, locations, placeholder 이름을 모은다."""
    found = {"db_types": {}, "locations": [], "placeholders_defined": set(), "schemas": set()}
    for f in files:
        name = f.name
        if not (name in ("flyway.conf", "flyway.toml") or re.match(r"application[-\w]*\.(ya?ml|properties)$", name)):
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        rel = str(f.relative_to(root))
        for line in text.splitlines():
            if re.search(r"\b(url|jdbc-url|jdbcUrl)\b\s*[:=]", line):
                kind = db_type(line)
                if kind:
                    found["db_types"].setdefault(kind, set()).add(rel)
            m = re.search(r"(?:flyway\.|liquibase\.|^\s*)locations\s*[:=]\s*(.+)$", line)
            if m and ("flyway" in line or "liquibase" in line or re.match(r"^\s+locations", line)) and "mapper" not in line:
                found["locations"].append({"file": rel, "value": m.group(1).strip().strip("\"'[]")})
            m = re.search(r"(?:flyway\.)?(?:schemas|default-schema|defaultSchema)\s*[:=]\s*([\w,\s]+)$", line)
            if m and ("flyway" in line or re.match(r"^\s+(schemas|default-schema)", line)):
                found["schemas"] |= {x.strip() for x in m.group(1).split(",") if x.strip()}
            m = re.search(r"currentSchema=(\w+)", line)
            if m:
                found["schemas"].add(m.group(1))
            m = re.search(r"placeholders\.([\w.-]+)\s*[:=]", line)
            if m:
                found["placeholders_defined"].add(m.group(1))
        for m in re.finditer(r"^\s{2,}placeholders\s*:\s*\n((?:\s{4,}[\w.-]+\s*:.*\n?)+)", text, re.M):
            found["placeholders_defined"] |= set(re.findall(r"^\s+([\w.-]+)\s*:", m.group(1), re.M))
    return found


def docker_images(files):
    """docker-compose 의 DB 이미지 태그. 재생 이미지 버전을 맞추는 데 쓴다."""
    out = set()
    for f in files:
        if re.match(r"(docker-)?compose[\w.-]*\.ya?ml$", f.name):
            out |= set(re.findall(r"image:\s*[\"']?((?:[\w.-]+/)*(?:postgres|mysql|mariadb|oracle-free|oracle-xe|database/free)[^\s\"']*)",
                                  f.read_text(errors="ignore")))
    return sorted(out)


def tool_dirs(files):
    """Supabase·Prisma·Drizzle 마이그레이션 디렉터리. [{format, path, steps, db_type}]"""
    out = []
    for f in files:
        if f.suffix == ".sql" and f.parent.name == "migrations" and f.parent.parent.name == "supabase" \
                and re.match(r"^\d{6,}_.+\.sql$", f.name) and not any(o["path"] == str(f.parent) for o in out):
            out.append({"format": "supabase", "path": str(f.parent), "db_type": "postgresql",
                        "steps": sum(1 for x in f.parent.glob("*.sql") if re.match(r"^\d{6,}_.+\.sql$", x.name))})
        elif f.name == "migration_lock.toml":
            m = re.search(r'^\s*provider\s*=\s*"([^"]+)"', f.read_text(errors="ignore"), re.M)
            provider = m.group(1) if m else None
            out.append({"format": "prisma", "path": str(f.parent), "provider": provider,
                        "db_type": "postgresql" if provider in ("postgresql", "postgres") else provider,
                        "steps": sum(1 for d in f.parent.iterdir() if (d / "migration.sql").exists())})
        elif f.name == "_journal.json" and f.parent.name == "meta":
            try:
                journal = json.loads(f.read_text())
            except ValueError:
                continue
            dialect = str(journal.get("dialect", "")).lower() or None
            out.append({"format": "drizzle", "path": str(f.parent.parent), "dialect": dialect,
                        "db_type": "postgresql" if dialect in ("postgresql", "postgres", "pg") else dialect,
                        "steps": len(journal.get("entries", []))})
    return sorted(out, key=lambda o: (o["format"], o["path"]))


def dirs_sql_hint(files):
    return any(f.suffix.lower() == ".sql" and re.match(r"^\d{6,}_+.+\.sql$", f.name) for f in files)


def main():
    early()
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    add_argument(parser)
    args = parser.parse_args()
    init_lang(args)
    root = args.path.resolve()
    if not root.exists():
        raise SystemExit(L(f"{root} 가 없습니다", f"{root} does not exist"))
    files = list(walk(root)) if root.is_dir() else [root]

    dirs = {}
    for f in files:
        if f.suffix.lower() == ".sql" and VERSIONED.match(f.name):
            dirs.setdefault(f.parent, {"versioned": 0})["versioned"] += 1
    migration_dirs = []
    placeholders_used = set()
    for d, info in sorted(dirs.items()):
        siblings = [p for p in d.iterdir() if p.is_file()]
        repeatable = sorted(p.name for p in siblings if p.name.startswith("R__"))
        callbacks = sorted(p.name for p in siblings if CALLBACK.match(p.name))
        java = sorted(p.name for p in siblings if re.match(r"^V\d", p.name) and p.suffix in (".java", ".kt", ".class"))
        for p in siblings:
            if p.suffix == ".sql":
                placeholders_used |= set(PLACEHOLDER.findall(p.read_text(errors="ignore")))
        migration_dirs.append({"path": str(d), "versioned": info["versioned"], "repeatable": repeatable,
                               "callbacks": callbacks, "code_migrations": java})

    journals = [] if not root.is_dir() else [j for j in root.rglob("meta/_journal.json")
                                             if not {".git", "node_modules"} & set(j.relative_to(root).parts)]
    tools = tool_dirs(files + [j for j in journals if j not in files])
    tool_paths = {t["path"] for t in tools}
    numbered = {}
    for f in files:
        if str(f.parent) in tool_paths:
            continue
        if f.suffix.lower() == ".sql" and re.match(r"^\d{6,}_+.+\.sql$", f.name):
            numbered.setdefault(f.parent, []).append(f)
    sql_dirs = []
    for d, fs in sorted(numbered.items()):
        if len(fs) < 2:
            continue
        head = "".join(f.read_text(errors="ignore")[:300] for f in fs[:5]).lower()
        init = [str(p.relative_to(d)) for p in d.rglob("init*.sql")]
        sql_dirs.append({"path": str(d), "files": len(fs) + len(init), "init": init,
                         "liquibase_formatted": "--changeset" in head or "--liquibase" in head})

    other = []
    if any(f.name in ("alembic.ini",) for f in files):
        other.append("alembic")
    if any(f.name == "schema.prisma" for f in files) and not any(t["format"] == "prisma" for t in tools):
        other.append(L("prisma(마이그레이션 이력 없음)", "prisma(no migration history)"))
    if any(re.search(r"changelog[\w.-]*\.(xml|ya?ml|json|sql)$", f.name, re.I) and "liquibase" in f.read_text(errors="ignore").lower()[:2000]
           for f in files if f.suffix.lower() in (".xml", ".yaml", ".yml", ".json", ".sql")):
        other.append("liquibase")

    cfg = read_configs(files, root)
    kinds = sorted(cfg["db_types"])
    if not kinds:
        kinds = sorted({t["db_type"] for t in tools if t["db_type"]})
    db = kinds[0] if len(kinds) == 1 else None
    warnings, notes = [], []
    flyway_dirs = {f.parent for f in files if re.match(r"^[VUBvub]\d+(?:[._]\d+)*__", f.name) and f.suffix.lower() == ".sql"}
    for d in sorted(flyway_dirs | set(dirs)):
        warnings += [f"{d}: {w}" for w in ignored_file_warnings([f for f in files if f.parent == d], d)]
    for d, info in sorted(dirs.items()):
        versions = [re.match(r"^V(\d+(?:[._]\d+)*)__", f.name).group(1).replace("_", ".")
                    for f in files if f.parent == d and VERSIONED.match(f.name)]
        notes += [f"{d}: {n}" for n in version_gaps(versions) + timestamp_order_notes(versions)]
    if not migration_dirs and not tools and not dirs_sql_hint(files):
        warnings.append(L("마이그레이션 이력(Flyway V 파일, supabase/migrations, prisma/migrations, drizzle journal)을 찾지 못했습니다.",
                          "No migration history found (Flyway V files, supabase/migrations, prisma/migrations, drizzle journal)."))
    if len(tools) + bool(migration_dirs) + bool(sql_dirs) > 1:
        warnings.append(L("마이그레이션 이력이 여러 곳입니다. 어느 이력을 볼지 사용자에게 확인하세요.",
                          "There are several migration histories. Ask the user which one to view."))
    for t in tools:
        if t["db_type"] and t["db_type"] not in ENGINES:
            warnings.append(L(f"{t['path']}: {t['format']} 의 DB 가 {t['db_type']} 입니다. 재생하지 않습니다(지원: PostgreSQL, MySQL, MariaDB, Oracle).",
                              f"{t['path']}: the {t['format']} database is {t['db_type']}, which is not replayed (supported: PostgreSQL, MySQL, MariaDB, Oracle)."))
        if t["format"] == "supabase":
            warnings.append(L(f"{t['path']}: Supabase 기본 스키마(auth·storage·역할)의 대역을 먼저 적용하고 재생합니다. "
                              "이미지에 없는 확장(pg_graphql, pgsodium 등)은 건너뜁니다.",
                              f"{t['path']}: a stand-in for the Supabase base schema (auth, storage, roles) is applied before replay. "
                              "Extensions missing from the image (pg_graphql, pgsodium, etc.) are skipped."))
    if len(migration_dirs) > 1:
        warnings.append(L("V 파일이 있는 디렉터리가 여러 곳입니다. 어느 디렉터리의 이력인지 사용자에게 확인하세요.",
                          "Several directories contain V files. Ask the user which directory holds the history."))
    if len(kinds) > 1:
        warnings.append(L(f"설정에 DB 종류가 여러 개입니다({', '.join(kinds)}). 사용자에게 확인하세요.",
                          f"The configuration names several database types ({', '.join(kinds)}). Ask the user."))
    if not kinds:
        warnings.append(L("설정에서 DB 종류를 찾지 못했습니다. 사용자에게 확인하세요.",
                          "No database type found in the configuration. Ask the user."))
    for d in migration_dirs:
        if d["repeatable"] or d["callbacks"]:
            warnings.append(L(f"{d['path']}: R__ 또는 SQL 콜백이 있습니다. 빼고 재생할지 사용자에게 확인하세요(--ignore-repeatable).",
                              f"{d['path']}: contains R__ files or SQL callbacks. Ask the user whether to replay without them (--ignore-repeatable)."))
        if d["code_migrations"]:
            warnings.append(L(f"{d['path']}: Java/Kotlin 마이그레이션은 재생하지 않습니다. 그 단계가 만드는 스키마는 빠집니다.",
                              f"{d['path']}: Java/Kotlin migrations are not replayed. The schema those steps create is missing."))
    undefined = sorted(placeholders_used - cfg["placeholders_defined"])
    if placeholders_used:
        warnings.append(L("Flyway placeholder(${...})가 있습니다. 지금은 치환하지 않고 적용하므로 재생이 실패할 수 있습니다.",
                          "Flyway placeholders (${...}) are present. They are applied without substitution, so the replay may fail."))
    if sql_dirs and not migration_dirs:
        lb = any(d["liquibase_formatted"] for d in sql_dirs)
        warnings.append(L("번호를 앞에 붙인 SQL 파일 이력입니다. --format sqlfiles 로 init 먼저, 이후 번호 순으로 적용합니다."
                          + (" Liquibase formatted SQL 이지만 Liquibase 를 실행하지 않고 파일 단위로 적용합니다." if lb else ""),
                          "This is a history of numbered SQL files. With --format sqlfiles, init files are applied first, then the rest in number order."
                          + (" It is Liquibase formatted SQL, but Liquibase is not run; each file is applied as a whole." if lb else "")))
    if len(cfg["schemas"]) == 1 and "public" not in cfg["schemas"]:
        schema = next(iter(cfg["schemas"]))
        warnings.append(L(f"설정의 기본 스키마가 {schema} 입니다. --schema 로 지정하세요.",
                          f"The default schema in the configuration is {schema}. Pass it with --schema."))
    pg_tools = [t for t in tools if t["db_type"] is None or t["db_type"] in ENGINES]
    supported = bool(migration_dirs or sql_dirs or pg_tools) and (db is None or db in ENGINES)
    if db and db not in ENGINES:
        warnings.append(L(f"{db} 는 아직 재생하지 않습니다. 지원: PostgreSQL, MySQL, MariaDB, Oracle.",
                          f"{db} is not replayed yet. Supported: PostgreSQL, MySQL, MariaDB, Oracle."))
    images = docker_images(files)
    if db == "mysql" and any(re.search(r"(^|/)mariadb", i) for i in images):
        warnings.append(L("설정은 mysql 이지만 docker-compose 이미지가 MariaDB 입니다. --db mariadb 로 재생할지 사용자에게 확인하세요.",
                          "The configuration says mysql but the docker-compose image is MariaDB. Ask the user whether to replay with --db mariadb."))
    if db in ("mysql", "mariadb"):
        warnings.append(L("MySQL·MariaDB 의 DDL 은 트랜잭션으로 묶이지 않습니다. 이름 변경은 단계 SQL 의 RENAME·CHANGE 문으로 판정합니다.",
                          "MySQL and MariaDB DDL is not transactional. Renames are determined from the RENAME and CHANGE statements in the step SQL."))
    if db == "oracle":
        warnings.append(L("Oracle 은 재생 동안 외부 경로가 없는 docker 내부 네트워크를 만들어 쓰고 끝나면 지웁니다. 첫 기동에 수십 초가 걸립니다.",
                          "For Oracle, replay creates a docker internal network with no external route and removes it afterwards. The first start takes tens of seconds."))
    if other and not migration_dirs and not sql_dirs and not tools:
        warnings.append(L(f"{', '.join(other)} 형식으로 보입니다. 지금은 재생하지 않습니다.",
                          f"This looks like {', '.join(other)}, which is not replayed yet."))

    result = {
        "format": ("flyway" if migration_dirs else pg_tools[0]["format"] if pg_tools else tools[0]["format"] if tools
                   else "sqlfiles" if sql_dirs else (other[0] if other else None)),
        "tool_dirs": tools,
        "sql_file_dirs": sql_dirs,
        "schema_candidates": sorted(cfg["schemas"]),
        "other_formats": other,
        "db_type": db, "db": ENGINES.get(db), "db_type_candidates": {k: sorted(v) for k, v in cfg["db_types"].items()},
        "migration_dirs": migration_dirs,
        "steps": sum(d["versioned"] for d in migration_dirs) or (pg_tools or tools or [{"steps": 0}])[0]["steps"]
                 or sum(d["files"] for d in sql_dirs),
        "locations": cfg["locations"],
        "placeholders_used": sorted(placeholders_used), "placeholders_undefined": undefined,
        "suggested_images": images,
        "supported": supported,
        "warnings": warnings,
        "notes": notes,
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
