"""MySQL·MariaDB 엔진. information_schema 로 스냅샷을 뜨고, 테이블 DDL 은 SHOW CREATE TABLE 결과를 쓴다.

MySQL 은 ALTER TABLE 로 테이블을 다시 만들면 내부 id 가 바뀌므로 고정 식별자가 없다(stable_ids False).
DDL 은 트랜잭션으로 묶이지 않는다. 단계가 중간에 실패하면 그 앞 문장까지는 적용된 상태로 남는다.
"""
import json
import re
import subprocess
import time

from i18n import L
from snapshot import remove_container, run, run_argv, run_step, track

EXCLUDED = ("flyway_schema_history", "DATABASECHANGELOG", "DATABASECHANGELOGLOCK", "databasechangelog", "databasechangeloglock",
            "_prisma_migrations", "__drizzle_migrations", "alembic_version", "schema_migrations")
NO_ACTION = ("RESTRICT", "NO ACTION")


def unescape_batch(line):
    """mysql -B 출력의 \\n \\t \\\\ \\0 이스케이프를 되돌린다."""
    out, i = [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            out.append({"n": "\n", "t": "\t", "\\": "\\", "0": "\0"}.get(line[i + 1], line[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def q(name):
    """식별자를 필요할 때만 백틱으로 감싼다."""
    return name if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", name) else "`" + name.replace("`", "``") + "`"


def lit(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "''") + "'"


class MySQL:
    label = "MySQL"
    default_schema = "replay"
    dialect = "mysql"
    stable_ids = False
    transactional_ddl = False
    has_roles = False
    has_extensions = False

    def __init__(self, variant="mysql"):
        self.name = variant
        self.mariadb = variant == "mariadb"
        self.label = "MariaDB" if self.mariadb else "MySQL"
        self.default_image = "mariadb:11.4" if self.mariadb else "mysql:8.4"
        self.client = "mariadb" if self.mariadb else "mysql"

    def start(self, image):
        env = ["-e", "MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=1"] if self.mariadb else ["-e", "MYSQL_ALLOW_EMPTY_PASSWORD=yes"]
        track()
        cid = run(run_argv(image, self.name, extra=["--tmpfs", "/var/lib/mysql:rw"] + env)).strip()
        try:
            for _ in range(120):
                logs = subprocess.run(["docker", "logs", cid], capture_output=True, text=True)
                text = logs.stdout + logs.stderr
                if re.search(r"ready for connections\.?(?:.|\n){0,300}?port: 3306\s", text):
                    ok = subprocess.run(["docker", "exec", cid, self.client, "-uroot", "-e", "SELECT 1"], capture_output=True)
                    if ok.returncode == 0:
                        return cid
                if logs.returncode != 0:
                    break
                time.sleep(1)
        except BaseException:
            remove_container(cid)
            raise
        remove_container(cid)
        raise RuntimeError(L(f"{self.label} 컨테이너가 120초 안에 준비되지 않았다", f"{self.label} container was not ready within 120 seconds"))

    def stop(self, cid):
        remove_container(cid)

    def db_for(self, schema):
        """MySQL 의 스키마는 데이터베이스다."""
        return schema

    def _client(self, cid, db, batch=False):
        cmd = ["docker", "exec", "-i", cid, self.client, "-uroot", "--default-character-set=utf8mb4"]
        if batch:
            cmd += ["-N", "-B"]
        return cmd + ([db] if db else [])

    def query(self, cid, db, sql):
        return run(self._client(cid, db, batch=True), stdin=sql)

    def create_db(self, cid, db, schema=None):
        run(self._client(cid, None), stdin=f"CREATE DATABASE {q(db)} CHARACTER SET utf8mb4;")

    def drop_db(self, cid, db, schema=None):
        run(self._client(cid, None), stdin=f"DROP DATABASE IF EXISTS {q(db)};")

    def apply(self, cid, db, sql, single_tx=False, schema=None):
        return run_step(self._client(cid, db), sql)

    def prepare_sql(self, sql):
        return sql, []

    def apply_warnings(self, proc):
        return []

    def error_text(self, stderr):
        return "\n".join(l for l in stderr.splitlines() if not l.startswith("mysql: [Warning]")).strip()

    def _rows(self, cid, db, sql):
        """태그를 붙인 JSON 행들을 {태그: [dict]} 로."""
        out = {}
        for line in self.query(cid, db, sql).splitlines():
            line = unescape_batch(line)
            tag, _, body = line.partition("\t")
            out.setdefault(tag, []).append(json.loads(body))
        return out

    def catalog(self, cid, db, schema):
        expr = "NULL" if self.mariadb else "EXPRESSION"
        check_table = "TABLE_NAME" if self.mariadb else "NULL"
        sql = f"""
SELECT CONCAT('T', CHAR(9), JSON_OBJECT('name', TABLE_NAME, 'comment', TABLE_COMMENT, 'collation', TABLE_COLLATION))
  FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE';
SELECT CONCAT('C', CHAR(9), JSON_OBJECT('table', c.TABLE_NAME, 'name', c.COLUMN_NAME, 'pos', c.ORDINAL_POSITION, 'type', c.COLUMN_TYPE,
       'nullable', c.IS_NULLABLE, 'default', c.COLUMN_DEFAULT, 'extra', c.EXTRA, 'gen', c.GENERATION_EXPRESSION,
       'comment', c.COLUMN_COMMENT, 'collation', c.COLLATION_NAME))
  FROM information_schema.COLUMNS c JOIN information_schema.TABLES t ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
 WHERE c.TABLE_SCHEMA = DATABASE() AND t.TABLE_TYPE = 'BASE TABLE' ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION;
SELECT CONCAT('K', CHAR(9), JSON_OBJECT('name', CONSTRAINT_NAME, 'table', TABLE_NAME, 'type', CONSTRAINT_TYPE))
  FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA = DATABASE();
SELECT CONCAT('U', CHAR(9), JSON_OBJECT('name', CONSTRAINT_NAME, 'table', TABLE_NAME, 'column', COLUMN_NAME, 'pos', ORDINAL_POSITION,
       'rschema', REFERENCED_TABLE_SCHEMA, 'rtable', REFERENCED_TABLE_NAME, 'rcolumn', REFERENCED_COLUMN_NAME))
  FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION;
SELECT CONCAT('R', CHAR(9), JSON_OBJECT('name', CONSTRAINT_NAME, 'table', TABLE_NAME, 'upd', UPDATE_RULE, 'del', DELETE_RULE))
  FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE();
SELECT CONCAT('H', CHAR(9), JSON_OBJECT('name', CONSTRAINT_NAME, 'table', {check_table}, 'clause', CHECK_CLAUSE))
  FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE();
SELECT CONCAT('I', CHAR(9), JSON_OBJECT('table', TABLE_NAME, 'name', INDEX_NAME, 'non_unique', NON_UNIQUE, 'seq', SEQ_IN_INDEX,
       'column', COLUMN_NAME, 'sub', SUB_PART, 'type', INDEX_TYPE, 'expr', {expr}, 'order', COLLATION))
  FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;
SELECT CONCAT('V', CHAR(9), JSON_OBJECT('name', TABLE_NAME, 'definition', VIEW_DEFINITION))
  FROM information_schema.VIEWS WHERE TABLE_SCHEMA = DATABASE();
"""
        r = self._rows(cid, db, sql)
        tables = {t["name"]: t for t in r.get("T", []) if t["name"] not in EXCLUDED}
        docs = {}
        for name, t in sorted(tables.items()):
            docs[name] = {"name": name, "comment": t["comment"] or None, "partition_by": None,
                          "columns": [], "constraints": [], "indexes": [], "_collation": t["collation"]}
        for c in r.get("C", []):
            t = docs.get(c["table"])
            if t:
                t["columns"].append(self._column(c, t["_collation"]))
        kinds = {"PRIMARY KEY": "p", "UNIQUE": "u", "FOREIGN KEY": "f", "CHECK": "c"}
        cons = {(k["table"], k["name"]): {"name": k["name"], "kind": kinds.get(k["type"], k["type"]), "columns": [],
                                         "ref_table": None, "ref_schema": None, "ref_columns": None}
                for k in r.get("K", []) if k["table"] in docs}
        for u in r.get("U", []):
            k = cons.get((u["table"], u["name"]))
            if not k:
                continue
            k["columns"].append(u["column"])
            if u.get("rtable"):
                other = u["rschema"] and u["rschema"] != db
                k["ref_schema"] = u["rschema"] if other else None
                k["ref_table"] = f"{u['rschema']}.{u['rtable']}" if other else u["rtable"]
                k["ref_columns"] = (k["ref_columns"] or []) + [u["rcolumn"]]
        rules = {(x["table"], x["name"]): x for x in r.get("R", [])}
        check_table = {k["name"]: k["table"] for k in r.get("K", []) if k["type"] == "CHECK"}
        for h in r.get("H", []):
            table = h.get("table") or check_table.get(h["name"])
            key = (table, h["name"])
            if table in docs:
                cons.setdefault(key, {"name": h["name"], "kind": "c", "columns": [], "ref_table": None, "ref_schema": None,
                                      "ref_columns": None})["clause"] = h["clause"]
        unique_names = set()
        for (table, name), k in sorted(cons.items()):
            if k["kind"] == "u":
                unique_names.add((table, name))
            k["definition"] = self._constraint_def(k, rules.get((table, name)))
            k.pop("clause", None)
            docs[table]["constraints"].append(k)
        idx = {}
        for i in r.get("I", []):
            if i["name"] == "PRIMARY" or (i["table"], i["name"]) in unique_names or i["table"] not in docs:
                continue
            x = idx.setdefault((i["table"], i["name"]), {"name": i["name"], "unique": not int(i["non_unique"]), "type": i["type"], "parts": []})
            x["parts"].append(i)
        for (table, name), x in sorted(idx.items()):
            parts = [((p["expr"] if p["expr"].startswith("(") and p["expr"].endswith(")") else f"({p['expr']})") if p.get("expr")
                      else q(p["column"]) + (f"({p['sub']})" if p.get("sub") else "")) + (" DESC" if p.get("order") == "D" else "")
                     for p in x["parts"]]
            head = "UNIQUE " if x["unique"] else "FULLTEXT " if x["type"] == "FULLTEXT" else "SPATIAL " if x["type"] == "SPATIAL" else ""
            docs[table]["indexes"].append({"name": name, "unique": x["unique"],
                                           "columns": [p.get("column") or p.get("expr") for p in x["parts"]],
                                           "definition": f"CREATE {head}INDEX {q(name)} ON {q(table)} ({', '.join(parts)})"})
        prefix = re.compile(r"`" + re.escape(db) + r"`\.")
        views = [{"name": v["name"], "definition": prefix.sub("", v["definition"] or ""), "depends": []}
                 for v in sorted(r.get("V", []), key=lambda v: v["name"])]
        for t in docs.values():
            t.pop("_collation")
        doc = {"tables": list(docs.values()), "views": views, "types": [], "schema_name": db}
        self._attach_create(cid, db, doc)
        return doc

    def _column(self, c, table_collation):
        extra = (c.get("extra") or "")
        low = extra.lower()
        default = c.get("default")
        if self.mariadb:
            default = None if default in (None, "NULL") else default
        elif default is not None and "default_generated" not in low and not (
                re.match(r"^(b'[01]*'|0x[0-9A-Fa-f]+)$", default) and re.match(r"^(bit|binary|varbinary|tinyblob|blob)\b", c["type"])):
            default = lit(default)
        m = re.search(r"on update (\S+)", extra, re.I)
        if m:
            default = f"{default or 'NULL'} ON UPDATE {m.group(1)}"
        generated = None
        if "generated" in low and c.get("gen"):
            generated = "stored" if "stored" in low else "virtual"
            default = c["gen"]
        coll = c.get("collation")
        return {"name": c["name"], "ordinal": c["pos"], "type": c["type"], "nullable": c["nullable"] == "YES",
                "identity": "auto_increment" if "auto_increment" in low else None, "generated": generated,
                "default": default, "owned_sequence": None,
                "collation": coll if coll and coll != table_collation else None, "comment": c.get("comment") or None}

    def _constraint_def(self, k, rule):
        cols = ", ".join(q(c) for c in k["columns"])
        if k["kind"] == "p":
            return f"PRIMARY KEY ({cols})"
        if k["kind"] == "u":
            return f"UNIQUE ({cols})"
        if k["kind"] == "c":
            clause = k.get("clause") or ""
            return f"CHECK ({clause})" if not (clause.startswith("(") and clause.endswith(")")) else f"CHECK {clause}"
        if k["kind"] == "f":
            ref = ".".join(q(x) for x in k["ref_table"].split(".", 1)) if k.get("ref_schema") else q(k["ref_table"])
            s = f"FOREIGN KEY ({cols}) REFERENCES {ref}({', '.join(q(c) for c in k['ref_columns'] or [])})"
            if rule and rule["del"] not in NO_ACTION:
                s += f" ON DELETE {rule['del']}"
            if rule and rule["upd"] not in NO_ACTION:
                s += f" ON UPDATE {rule['upd']}"
            return s
        return k["kind"]

    def _attach_create(self, cid, db, doc):
        """SHOW CREATE TABLE 결과를 테이블의 ddl 로. AUTO_INCREMENT 카운터는 데이터에 따라 바뀌므로 뺀다."""
        if not doc["tables"]:
            return
        sql = "".join(f"SHOW CREATE TABLE {q(t['name'])};\n" for t in doc["tables"])
        ddl = {}
        for line in self.query(cid, db, sql).splitlines():
            name, _, text = unescape_batch(line).partition("\t")
            ddl[name] = re.sub(r" AUTO_INCREMENT=\d+", "", text).rstrip() + ";"
        for t in doc["tables"]:
            t["ddl"] = ddl.get(t["name"], "")

    def objects(self, cid, db, schema):
        """데이터베이스 안의 함수·프로시저·트리거·이벤트(MariaDB 는 시퀀스도).

        지문은 루틴은 정의·반환 타입·매개변수·속성, 트리거는 시점·이벤트·본문, 이벤트는 본문·일정·상태의 md5 다.
        시퀀스는 지문이 없어 changed 로 잡지 않는다.
        """
        sql = """
SELECT CONCAT('O', CHAR(9), JSON_OBJECT('type', r.ROUTINE_TYPE, 'name', r.ROUTINE_NAME, 'table', NULL, 'status', NULL,
       'fp', MD5(CONCAT_WS('|', r.ROUTINE_DEFINITION, r.DTD_IDENTIFIER, r.IS_DETERMINISTIC, r.SQL_DATA_ACCESS, r.SECURITY_TYPE,
       (SELECT GROUP_CONCAT(CONCAT_WS(' ', p.PARAMETER_MODE, p.PARAMETER_NAME, p.DTD_IDENTIFIER) ORDER BY p.ORDINAL_POSITION SEPARATOR ',')
          FROM information_schema.PARAMETERS p WHERE p.SPECIFIC_SCHEMA = r.ROUTINE_SCHEMA AND p.SPECIFIC_NAME = r.SPECIFIC_NAME)))))
  FROM information_schema.ROUTINES r WHERE r.ROUTINE_SCHEMA = DATABASE();
SELECT CONCAT('O', CHAR(9), JSON_OBJECT('type', 'TRIGGER', 'name', TRIGGER_NAME, 'table', EVENT_OBJECT_TABLE, 'status', NULL,
       'fp', MD5(CONCAT_WS('|', ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT))))
  FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE();
SELECT CONCAT('O', CHAR(9), JSON_OBJECT('type', 'EVENT', 'name', EVENT_NAME, 'table', NULL, 'status', NULL,
       'fp', MD5(CONCAT_WS('|', EVENT_DEFINITION, EVENT_TYPE, EXECUTE_AT, INTERVAL_VALUE, INTERVAL_FIELD, STATUS))))
  FROM information_schema.EVENTS WHERE EVENT_SCHEMA = DATABASE();
SELECT CONCAT('O', CHAR(9), JSON_OBJECT('type', 'SEQUENCE', 'name', TABLE_NAME, 'table', NULL, 'status', NULL, 'fp', NULL))
  FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'SEQUENCE';
"""
        return self._rows(cid, db, sql).get("O", [])

    def add_ddl(self, doc):
        """뷰 ddl 과 실행 순서 객체 목록(schema)을 채운다. 테이블 ddl 은 스냅샷을 뜰 때 DB 에서 받은 것이다."""
        for v in doc["views"]:
            v["ddl"] = f"CREATE VIEW {q(v['name'])} AS {v['definition'].strip().rstrip(';')};"
        objs = [{"kind": "setup", "name": "fk_checks_off", "text": "SET FOREIGN_KEY_CHECKS = 0;"}]
        objs += [{"kind": "table", "name": t["name"], "text": t.get("ddl", "")} for t in doc["tables"]]
        objs += [{"kind": "view", "name": v["name"], "text": v["ddl"]} for v in doc["views"]]
        objs.append({"kind": "setup", "name": "fk_checks_on", "text": "SET FOREIGN_KEY_CHECKS = 1;"})
        doc["schema"] = objs
        return doc
