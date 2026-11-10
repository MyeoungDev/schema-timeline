"""PostgreSQL 카탈로그 스냅샷으로 사람이 읽기 좋은 CREATE TABLE / CREATE VIEW 문을 만든다.

pg_dump 결과와 글자 단위로 같지 않다. 컬럼이 소유한 시퀀스를 nextval 하는 기본값은 SERIAL 로,
character varying 은 VARCHAR 로 되돌리고, 한 컬럼짜리 PK·UNIQUE·FK 는 컬럼 줄에 붙인다.
diff 가 줄 단위로 안정되도록 열 맞춤 공백은 넣지 않는다.
enum 타입은 담고, domain·함수·확장·시퀀스 옵션은 담지 않는다.
"""
import re

# SELECT word FROM pg_get_keywords() WHERE catcode <> 'U' (PostgreSQL 16)
RESERVED = set("""all analyse analyze and any array as asc asymmetric authorization between bigint binary bit boolean both case
cast char character check coalesce collate collation column concurrently constraint create cross current_catalog
current_date current_role current_schema current_time current_timestamp current_user dec decimal default deferrable desc
distinct do else end except exists extract false fetch float for foreign freeze from full grant greatest group grouping
having ilike in initially inner inout int integer intersect interval into is isnull join json_array json_arrayagg
json_object json_objectagg lateral leading least left like limit localtime localtimestamp national natural nchar none
normalize not notnull null nullif numeric offset on only or order out outer overlaps overlay placing position precision
primary real references returning right row select session_user setof similar smallint some substring symmetric
system_user table tablesample then time timestamp to trailing treat trim true union unique user using values varchar
variadic verbose when where window with xmlattributes xmlconcat xmlelement xmlexists xmlforest xmlnamespaces xmlparse
xmlpi xmlroot xmlserialize xmltable""".split())
TYPE_PATTERNS = [
    (r"character varying(\(\d+\))?", r"VARCHAR\1"), (r"character(\(\d+\))?", r"CHAR\1"),
    (r"timestamp(\(\d+\))? without time zone", r"TIMESTAMP\1"), (r"timestamp(\(\d+\))? with time zone", r"TIMESTAMPTZ\1"),
    (r"time(\(\d+\))? without time zone", r"TIME\1"), (r"time(\(\d+\))? with time zone", r"TIMETZ\1"),
    (r"double precision", "DOUBLE PRECISION"),
]
BUILTIN = {"smallint", "integer", "bigint", "numeric", "real", "boolean", "text", "date", "interval", "uuid", "json",
           "jsonb", "bytea", "inet", "cidr", "xml", "money", "bit", "tsvector"}
SERIAL = {"smallint": "SMALLSERIAL", "integer": "SERIAL", "bigint": "BIGSERIAL"}
FK_HEAD = re.compile(r"^FOREIGN KEY \(.*?\) REFERENCES .*?\(.*?\)\s*")
LONE_LITERAL = re.compile(r"^('(?:[^']|'')*')::[a-z][a-z ]*(?:\(\d+(?:,\d+)?\))?(?:\[\])?$")
# 컬럼 제약 문법이 받지 않는 인덱스 옵션. 이런 PK·UNIQUE 는 테이블 수준 제약으로 둔다.
INDEX_OPTIONS = re.compile(r"\b(INCLUDE|USING INDEX|WITH)\s*\(|\bUSING INDEX TABLESPACE\b")
NEXTVAL = re.compile(r"^nextval\('((?:[^']|'')+)'::regclass\)$")


def ident(name):
    """필요할 때만 큰따옴표로 감싼 식별자."""
    if re.fullmatch(r"[a-z_][a-z0-9_$]*", name) and name not in RESERVED:
        return name
    return '"' + name.replace('"', '""') + '"'


def type_text(t):
    """format_type 결과를 대문자 관용 표기로 바꾼다. 알 수 없는 타입(enum·domain)은 그대로 둔다."""
    base, arr = re.fullmatch(r"(.*?)((?:\[\])*)", t).groups()
    for pat, rep in TYPE_PATTERNS:
        m = re.fullmatch(pat, base)
        if m:
            return m.expand(rep) + arr
    m = re.fullmatch(r"([a-z]+)(\(.*\))?", base)
    if m and m.group(1) in BUILTIN:
        return m.group(1).upper() + (m.group(2) or "") + arr
    return t


def default_text(d):
    """기본값이 리터럴 하나에 형 변환만 붙은 것('A'::character varying)이면 형 변환을 뗀다. 그 밖은 그대로 둔다."""
    m = LONE_LITERAL.match(d)
    return m.group(1) if m else d


def default_name(table, k):
    """PostgreSQL 이 제약에 자동으로 붙이는 이름. 이 이름이면 CONSTRAINT 절을 생략한다."""
    cols = "_".join(k["columns"] or [])
    return {"p": f"{table}_pkey", "u": f"{table}_{cols}_key", "f": f"{table}_{cols}_fkey",
            "c": f"{table}_{cols}_check" if cols else f"{table}_check"}.get(k["kind"])


def is_serial(c):
    """컬럼이 소유한 시퀀스를 nextval 하는 정수 컬럼인지. identity 컬럼은 아니다."""
    m = NEXTVAL.match(c.get("default") or "")
    seq = c.get("owned_sequence")
    if not (m and seq and c["type"] in SERIAL and not c.get("identity")):
        return False
    return m.group(1).replace("''", "'").split(".")[-1].strip('"') == seq.split(".")[-1].strip('"')


def inline_text(k):
    """한 컬럼짜리 PK·UNIQUE 를 컬럼 제약 형태로. 컬럼 목록만 빼고 옵션(DEFERRABLE, INCLUDE 등)은 남긴다."""
    return re.sub(r"\s*\([^()]*\)", "", k["definition"], count=1)


def fk_text(k):
    """FK 를 컬럼 제약 형태의 REFERENCES 절로. 다른 스키마의 테이블은 스키마 이름을 붙인다."""
    opts = FK_HEAD.sub("", k["definition"])
    target = ident(k["ref_table"])
    if k.get("ref_schema") and k["ref_table"].startswith(k["ref_schema"] + "."):
        target = f"{ident(k['ref_schema'])}.{ident(k['ref_table'][len(k['ref_schema']) + 1:])}"
    ref = f"REFERENCES {target}({', '.join(ident(c) for c in k['ref_columns'] or [])})"
    return ref + (f" {opts}" if opts else "")


def table_ddl(t, skip=()):
    """테이블 하나의 CREATE TABLE 문과 그 뒤의 인덱스·코멘트 문.

    NOT VALID 제약은 CREATE TABLE 안에서는 검증된 제약이 되므로 뒤에 ALTER TABLE 로 붙인다.
    @param skip 여기서 빼고 호출한 쪽이 따로 붙일 제약 이름
    """
    name = t["name"]
    inline, table_level, sequences, after = {}, [], [], []
    for k in t["constraints"]:
        if k["name"] in skip:
            continue
        if "NOT VALID" in k["definition"]:
            after.append(add_constraint(name, k))
            continue
        cols = k["columns"] or []
        named = "" if k["name"] == default_name(name, k) else f"CONSTRAINT {ident(k['name'])} "
        if len(cols) == 1 and k["kind"] in ("p", "u", "f") and not INDEX_OPTIONS.search(k["definition"]):
            inline.setdefault(cols[0], []).append(named + (fk_text(k) if k["kind"] == "f" else inline_text(k)))
        else:
            table_level.append(named + k["definition"])

    lines = []
    for c in t["columns"]:
        tags = inline.get(c["name"], [])
        serial = is_serial(c)
        parts = [ident(c["name"]), SERIAL[c["type"]] if serial else type_text(c["type"])]
        if c.get("collation"):
            parts.append(f"COLLATE {c['collation']}")
        if c.get("identity"):
            parts.append("GENERATED ALWAYS AS IDENTITY" if c["identity"] == "a" else "GENERATED BY DEFAULT AS IDENTITY")
        if not c["nullable"] and not any(x.endswith("PRIMARY KEY") or " PRIMARY KEY" in x or x.startswith("PRIMARY KEY") for x in tags):
            parts.append("NOT NULL")
        d = c.get("default")
        if d and c.get("generated") == "s":
            parts.append(f"GENERATED ALWAYS AS ({d}) STORED")
        elif d and not serial:
            parts.append(f"DEFAULT {default_text(d)}")
            m = NEXTVAL.match(d)
            if m and not c.get("owned_sequence"):
                sequences.append(m.group(1).replace("''", "'"))
        parts += tags
        lines.append(" ".join(parts))
    lines += table_level
    body = ",\n".join("    " + l for l in lines)
    head = "".join(f"CREATE SEQUENCE IF NOT EXISTS {s};\n" for s in sequences)
    tail = f" PARTITION BY {t['partition_by']}" if t.get("partition_by") else ""
    out = [f"{head}CREATE TABLE {ident(name)} (\n{body}\n){tail};"] + after
    for i in t["indexes"]:
        out.append(re.sub(r" ON (ONLY )?(?:\w+|\"(?:[^\"]|\"\")+\")\.", r" ON \1", i["definition"], count=1).replace(" USING btree ", " ") + ";")
    if t.get("comment"):
        out.append(f"COMMENT ON TABLE {ident(name)} IS {quote(t['comment'])};")
    for c in t["columns"]:
        if c.get("comment"):
            out.append(f"COMMENT ON COLUMN {ident(name)}.{ident(c['name'])} IS {quote(c['comment'])};")
    return "\n".join(out)


def add_constraint(table, k):
    return f"ALTER TABLE {ident(table)} ADD CONSTRAINT {ident(k['name'])} {k['definition']};"


def view_ddl(v):
    return f"CREATE VIEW {ident(v['name'])} AS\n{v['definition'].strip().rstrip(';')};"


def type_ddl(ty):
    """enum 타입 정의문."""
    return f"CREATE TYPE {ident(ty['name'])} AS ENUM ({', '.join(quote(x) for x in ty.get('labels') or [])});"


def quote(s):
    return "'" + s.replace("'", "''") + "'"


def schema_objects(snapshot):
    """새 DB 에 그대로 실행할 수 있는 순서의 객체 목록 [{kind, name, text}].

    enum 타입을 맨 앞에, 참조되는 테이블을 먼저 두고, 순환 참조로 앞에 둘 수 없는 FK 는 마지막에 ALTER TABLE 로 붙인다.
    뷰는 참조하는 뷰보다 뒤에 둔다.
    """
    tables = {t["name"]: t for t in snapshot["tables"]}
    order, done = [], set()
    deferred = {n: [k for k in t["constraints"] if "NOT VALID" in k["definition"]] for n, t in tables.items()}
    pending = sorted(tables)
    while pending:
        ready = [n for n in pending if all(k["ref_table"] in done or k["ref_table"] == n or k["ref_table"] not in tables
                                          for k in tables[n]["constraints"] if k["kind"] == "f" and k not in deferred[n])]
        if not ready:
            n = pending[0]
            deferred[n] += [k for k in tables[n]["constraints"] if k["kind"] == "f" and k["ref_table"] not in done
                            and k["ref_table"] != n and k["ref_table"] in tables and k not in deferred[n]]
            ready = [n]
        for n in ready:
            order.append(n); done.add(n); pending.remove(n)
    objs = [{"kind": "table", "name": n, "text": table_ddl(tables[n], {k["name"] for k in deferred.get(n, [])})} for n in order]
    for n in order:
        for k in deferred[n]:
            objs.append({"kind": "constraint", "name": n, "text": add_constraint(n, k)})
    objs[0:0] = [{"kind": "type", "name": ty["name"], "text": type_ddl(ty)} for ty in snapshot.get("types", [])]
    schema = snapshot.get("schema_name") or "public"
    if schema != "public":
        objs.insert(0, {"kind": "setup", "name": schema,
                        "text": f"CREATE SCHEMA IF NOT EXISTS {ident(schema)};\nSET search_path TO {ident(schema)}, public;"})
    views = {v["name"]: v for v in snapshot["views"]}
    vdone, vpending = set(), sorted(views)
    while vpending:
        ready = [n for n in vpending if all(d in vdone or d not in views for d in views[n].get("depends", []))] or vpending[:1]
        for n in ready:
            objs.append({"kind": "view", "name": n, "text": view_ddl(views[n])}); vdone.add(n); vpending.remove(n)
    return objs


def add_ddl(snapshot):
    """스냅샷의 테이블·뷰마다 ddl 필드를, 스냅샷에 schema(실행 순서 객체 목록)를 채운다."""
    for t in snapshot["tables"]:
        t["ddl"] = table_ddl(t)
    for v in snapshot["views"]:
        v["ddl"] = view_ddl(v)
    snapshot["schema"] = schema_objects(snapshot)
    return snapshot
