"""Oracle 엔진(gvenzl/oracle-free 이미지). 스키마는 재생용 사용자 하나이고, USER_* 딕셔너리 뷰로 스냅샷을 뜬다.

Oracle 23 이상은 사설 IP 가 없으면 뜨지 않으므로 --network none 대신 재생마다 외부로 나가는 경로가 없는
docker 내부 네트워크(--internal)를 만들어 쓰고, 끝나면 컨테이너와 함께 지운다.
테이블 DDL 은 DBMS_METADATA.GET_DDL 결과에 인덱스·코멘트 문을 덧붙인 것이다.
DDL 은 트랜잭션으로 묶이지 않는다(문장마다 자동 커밋).
"""
import json
import re
import subprocess
import time
import uuid

from i18n import L
from scriptsplit import oracle_script
from snapshot import LABEL, RUN_ID, remove_container, run, run_argv, run_step, track

PASSWORD = "replay_pw"
SERVICE = "//localhost/FREEPDB1"
EXCLUDED = ("FLYWAY_SCHEMA_HISTORY", "flyway_schema_history", "DATABASECHANGELOG", "DATABASECHANGELOGLOCK")
APPLY_HEAD = """WHENEVER SQLERROR EXIT FAILURE
WHENEVER OSERROR EXIT FAILURE
SET DEFINE OFF
SET SQLBLANKLINES ON
SET FEEDBACK ON
SET ECHO OFF
"""
QUERY_HEAD = """WHENEVER SQLERROR EXIT FAILURE
SET DEFINE OFF
SET PAGESIZE 0 LINESIZE 32767 LONG 10000000 LONGCHUNKSIZE 10000000 TRIMSPOOL ON TRIMOUT ON FEEDBACK OFF HEADING OFF
SET SERVEROUTPUT OFF VERIFY OFF TAB OFF
"""
TAGS = ("T", "C", "K", "N", "I", "V", "D", "M", "O")


def q(name):
    """대문자 일반 식별자가 아니면 큰따옴표로 감싼다."""
    return name if re.match(r"^[A-Z][A-Z0-9_$#]*$", name) else '"' + name.replace('"', '""') + '"'


def type_text(c):
    """USER_TAB_COLS 의 타입 정보를 DDL 표기로."""
    t, p, s = c["type"], c.get("p"), c.get("s")
    if t == "NUMBER":
        if p is None:
            return "INTEGER" if s == 0 else "NUMBER"
        return f"NUMBER({p})" if not s else f"NUMBER({p},{s})"
    if t in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
        return f"{t}({c['len']}{' CHAR' if c.get('cu') == 'C' and t in ('VARCHAR2', 'CHAR') else ''})"
    if t in ("RAW", "UROWID"):
        return f"{t}({c['dl']})"
    if t == "FLOAT" and p:
        return f"FLOAT({p})"
    return t


class Oracle:
    name = "oracle"
    label = "Oracle"
    default_image = "gvenzl/oracle-free:23-slim"
    default_schema = "REPLAY"
    dialect = "oracle"
    stable_ids = False
    transactional_ddl = False
    has_roles = False
    has_extensions = False

    def __init__(self):
        self.networks = {}

    def start(self, image):
        net = f"{LABEL}-{uuid.uuid4().hex[:8]}"
        track()
        run(["docker", "network", "create", "--internal", "--label", f"{LABEL}=1", "--label", f"{LABEL}.run={RUN_ID}", net])
        try:
            cid = run(run_argv(image, "oracle", network=net, rm=False, extra=["-e", f"ORACLE_PASSWORD={PASSWORD}"])).strip()
        except BaseException:
            run(["docker", "network", "rm", net], check=False)
            raise
        self.networks[cid] = net
        try:
            for _ in range(180):
                logs = subprocess.run(["docker", "logs", cid], capture_output=True, text=True)
                if "DATABASE IS READY TO USE" in logs.stdout + logs.stderr:
                    return cid
                state = run(["docker", "inspect", "-f", "{{.State.Running}}", cid], check=False).strip()
                if state != "true":
                    break
                time.sleep(2)
            tail = (logs.stdout + logs.stderr)[-800:]
        except BaseException:
            self.stop(cid)
            raise
        self.stop(cid)
        raise RuntimeError(L(f"Oracle 컨테이너가 준비되지 않았다. 컨테이너 로그 끝:\n{tail}", f"Oracle container was not ready. End of container log:\n{tail}"))

    def stop(self, cid):
        """컨테이너를 지우고 내부 네트워크를 지운다. 일찍 죽어도 로그를 읽을 수 있게 --rm 없이 띄우므로 여기서 지운다."""
        remove_container(cid)
        net = self.networks.pop(cid, None)
        if net:
            for _ in range(10):
                if subprocess.run(["docker", "network", "rm", net], capture_output=True).returncode == 0:
                    break
                time.sleep(1)

    def db_for(self, schema):
        return schema.upper() if re.match(r"^[A-Za-z][A-Za-z0-9_$#]*$", schema) else schema

    def _sqlplus(self, cid, user, script, timed=False):
        """sqlplus 로 스크립트를 실행한다. timed 면 단계 제한 시간을 적용한다(StepTimeout)."""
        cmd = ["docker", "exec", "-i", cid, "sqlplus", "-S", "-L", f"{user}/{PASSWORD}@{SERVICE}"]
        if timed:
            return run_step(cmd, script + "\nEXIT\n")
        return subprocess.run(cmd, input=script + "\nEXIT\n", capture_output=True, text=True)

    def _system(self, cid, sql):
        proc = self._sqlplus(cid, "system", "WHENEVER SQLERROR EXIT FAILURE\n" + sql)
        if proc.returncode != 0:
            raise RuntimeError(L("Oracle 관리 명령 실패\n", "Oracle admin command failed\n") + proc.stdout.strip()[-800:])

    def create_db(self, cid, db, schema=None):
        self._system(cid, f"CREATE USER {q(db)} IDENTIFIED BY {PASSWORD} QUOTA UNLIMITED ON USERS;\n"
                          f"GRANT DB_DEVELOPER_ROLE TO {q(db)};")

    def drop_db(self, cid, db, schema=None):
        self._system(cid, f"BEGIN EXECUTE IMMEDIATE 'DROP USER {q(db)} CASCADE'; EXCEPTION WHEN OTHERS THEN "
                          f"IF SQLCODE != -1918 THEN RAISE; END IF; END;\n/")

    def apply(self, cid, db, sql, single_tx=False, schema=None):
        """sqlplus 로 적용한다. sqlplus 는 오류를 표준 출력에 쓰므로 실패하면 그것을 stderr 로 옮긴다."""
        proc = self._sqlplus(cid, db, APPLY_HEAD + sql.rstrip() + "\n", timed=True)
        if proc.returncode != 0:
            proc = subprocess.CompletedProcess(proc.args, proc.returncode, "", (proc.stdout + proc.stderr).strip())
        return proc

    def prepare_sql(self, sql):
        """sqlplus 정규형으로. 마지막 문장의 ; 누락, SQL 뒤 / 의 재실행, SQL*Plus 명령 줄을 정리한다."""
        return oracle_script(sql)

    def apply_warnings(self, proc):
        """컴파일 오류가 있는 PL/SQL 객체. sqlplus 는 이것을 오류가 아닌 경고로 내고 계속 진행한다."""
        return [L("컴파일 오류가 있는 객체를 만들었습니다: ", "Created an object with compilation errors: ") + m.group(1)
                for m in re.finditer(r"Warning: (.+?) created with compilation errors", proc.stdout or "")]

    def error_text(self, stderr):
        """sqlplus 출력에서 성공 메시지(FEEDBACK)와 도움말 링크를 뺀 오류 부분."""
        feedback = re.compile(r"^(?:[\w/ ]+ (?:created|altered|dropped|renamed|succeeded|completed|truncated)|Commit complete"
                              r"|\d+ rows? (?:inserted|updated|deleted|merged|selected|created))\.$", re.I)
        lines = [l for l in stderr.splitlines()
                 if l.strip() and not l.startswith("Help: https://docs.oracle.com") and not feedback.match(l.strip())]
        return "\n".join(lines[-12:])

    def query(self, cid, db, sql):
        proc = self._sqlplus(cid, db, QUERY_HEAD + sql)
        if proc.returncode != 0:
            raise RuntimeError(L("Oracle 카탈로그 조회 실패\n", "Oracle catalog query failed\n") + proc.stdout.strip()[-800:])
        return proc.stdout

    def _rows(self, cid, db, sql):
        """태그+탭으로 시작하는 JSON 행. sqlplus 가 줄을 접은 경우 다음 줄을 이어 붙인다."""
        out, cur = {}, None
        for line in self.query(cid, db, sql).splitlines():
            tag, tab, body = line.partition("\t")
            if tab and tag in TAGS:
                cur = [tag, body]
                out.setdefault(tag, []).append(cur)
            elif cur is not None:
                cur[1] += line
        return {k: [json.loads(b) for _, b in v] for k, v in out.items()}

    def catalog(self, cid, db, schema):
        sql = """
SELECT 'T' || CHR(9) || JSON_OBJECT('name' VALUE t.table_name, 'comment' VALUE c.comments RETURNING CLOB)
  FROM user_tables t LEFT JOIN user_tab_comments c ON c.table_name = t.table_name
 WHERE t.nested = 'NO' AND t.secondary = 'N' AND t.table_name NOT LIKE 'BIN$%' AND t.table_name NOT LIKE 'DR$%';
SELECT 'C' || CHR(9) || JSON_OBJECT('table' VALUE c.table_name, 'name' VALUE c.column_name, 'pos' VALUE c.column_id,
       'iid' VALUE c.internal_column_id, 'type' VALUE c.data_type, 'len' VALUE c.char_length, 'dl' VALUE c.data_length,
       'cu' VALUE c.char_used, 'p' VALUE c.data_precision, 's' VALUE c.data_scale, 'nullable' VALUE c.nullable,
       'default' VALUE c.data_default_vc, 'virtual' VALUE c.virtual_column, 'hidden' VALUE c.hidden_column,
       'identity' VALUE i.generation_type, 'collation' VALUE c.collation, 'comment' VALUE m.comments RETURNING CLOB)
  FROM user_tab_cols c
  LEFT JOIN user_tab_identity_cols i ON i.table_name = c.table_name AND i.column_name = c.column_name
  LEFT JOIN user_col_comments m ON m.table_name = c.table_name AND m.column_name = c.column_name
 ORDER BY c.table_name, c.column_id;
SELECT 'K' || CHR(9) || JSON_OBJECT('name' VALUE k.constraint_name, 'table' VALUE k.table_name, 'type' VALUE k.constraint_type,
       'cond' VALUE k.search_condition_vc, 'r' VALUE k.r_constraint_name, 'del' VALUE k.delete_rule, 'gen' VALUE k.generated,
       'index' VALUE k.index_name, 'status' VALUE k.status, 'validated' VALUE k.validated RETURNING CLOB)
  FROM user_constraints k WHERE k.constraint_type IN ('P', 'U', 'R', 'C') AND k.table_name NOT LIKE 'BIN$%';
SELECT 'N' || CHR(9) || JSON_OBJECT('name' VALUE constraint_name, 'table' VALUE table_name, 'column' VALUE column_name,
       'pos' VALUE position RETURNING CLOB)
  FROM user_cons_columns ORDER BY constraint_name, position;
SELECT 'I' || CHR(9) || JSON_OBJECT('name' VALUE i.index_name, 'table' VALUE i.table_name, 'unique' VALUE i.uniqueness,
       'type' VALUE i.index_type, 'gen' VALUE i.generated, 'column' VALUE ic.column_name, 'pos' VALUE ic.column_position,
       'desc' VALUE ic.descend RETURNING CLOB)
  FROM user_indexes i JOIN user_ind_columns ic ON ic.index_name = i.index_name
 WHERE i.index_type NOT IN ('LOB', 'IOT - TOP') ORDER BY i.index_name, ic.column_position;
SELECT 'V' || CHR(9) || JSON_OBJECT('name' VALUE view_name, 'definition' VALUE text_vc RETURNING CLOB) FROM user_views;
"""
        r = self._rows(cid, db, sql)
        docs = {}
        for t in sorted(r.get("T", []), key=lambda t: t["name"]):
            if t["name"] not in EXCLUDED:
                docs[t["name"]] = {"name": t["name"], "comment": t.get("comment"), "partition_by": None,
                                   "columns": [], "constraints": [], "indexes": []}
        hidden = {}
        for c in r.get("C", []):
            if c["hidden"] == "YES":
                hidden[(c["table"], c["name"])] = (c.get("default") or "").strip()
            elif c["table"] in docs:
                docs[c["table"]]["columns"].append(self._column(c))
        cols = {}
        for n in r.get("N", []):
            cols.setdefault(n["name"], []).append(n["column"])
        cons = {k["name"]: k for k in r.get("K", [])}
        backing = set()
        for k in sorted(r.get("K", []), key=lambda k: (k["table"], k["type"], k["name"])):
            t = docs.get(k["table"])
            if not t:
                continue
            kc = cols.get(k["name"], [])
            if k["type"] == "C" and k["gen"] == "GENERATED NAME" and re.match(r'^"[^"]+" IS NOT NULL$', (k.get("cond") or "").strip()):
                continue
            if k.get("index"):
                backing.add(k["index"])
            kind = {"P": "p", "U": "u", "R": "f", "C": "c"}[k["type"]]
            item = {"name": k["name"], "kind": kind, "columns": kc, "ref_table": None, "ref_schema": None, "ref_columns": None}
            if kind == "f":
                ref = cons.get(k["r"]) or {}
                item["ref_table"] = ref.get("table")
                item["ref_columns"] = cols.get(k["r"], [])
            item["definition"] = self._constraint_def(item, k)
            t["constraints"].append(item)
        idx = {}
        for i in r.get("I", []):
            if i["name"] in backing or i["gen"] == "Y" or i["table"] not in docs:
                continue
            x = idx.setdefault(i["name"], {"table": i["table"], "unique": i["unique"] == "UNIQUE", "type": i["type"], "parts": []})
            col = i["column"]
            expr = hidden.get((i["table"], col)) if col.startswith("SYS_NC") else None
            part = expr if expr else q(col)
            x["parts"].append(part + (" DESC" if i.get("desc") == "DESC" else ""))
        for name, x in sorted(idx.items()):
            head = "UNIQUE " if x["unique"] else "BITMAP " if x["type"].startswith("BITMAP") else ""
            docs[x["table"]]["indexes"].append({"name": name, "unique": x["unique"], "columns": x["parts"],
                                                "definition": f"CREATE {head}INDEX {q(name)} ON {q(x['table'])} ({', '.join(x['parts'])})"})
        views = [{"name": v["name"], "definition": (v.get("definition") or "").strip(), "depends": []}
                 for v in sorted(r.get("V", []), key=lambda v: v["name"])]
        doc = {"tables": list(docs.values()), "views": views, "types": [], "schema_name": db}
        self._attach_ddl(cid, db, doc)
        return doc

    def objects(self, cid, db, schema):
        """사용자 소유의 PL/SQL 객체·시퀀스·시노님 등. 테이블·인덱스·뷰·LOB·파티션과 시스템이 만든 객체(GENERATED, BIN$, ISEQ$$)는 뺀다.

        지문은 USER_SOURCE 의 줄 수와 줄별 ORA_HASH 합, 시퀀스·시노님은 속성 문자열이다. status 는 USER_OBJECTS.STATUS 다.
        """
        sql = r"""
SELECT 'O' || CHR(9) || JSON_OBJECT('type' VALUE o.object_type, 'name' VALUE o.object_name, 'table' VALUE tr.table_name,
       'status' VALUE CASE WHEN o.status IN ('VALID', 'INVALID') THEN o.status END,
       'fp' VALUE COALESCE(s.fp, q.fp, y.fp) RETURNING CLOB)
  FROM user_objects o
  LEFT JOIN user_triggers tr ON o.object_type = 'TRIGGER' AND tr.trigger_name = o.object_name
  LEFT JOIN (SELECT name, type, COUNT(*) || ':' || SUM(ORA_HASH(line || ':' || text)) AS fp
               FROM user_source GROUP BY name, type) s ON s.name = o.object_name AND s.type = o.object_type
  LEFT JOIN (SELECT sequence_name, min_value || '|' || max_value || '|' || increment_by || '|' || cycle_flag || '|'
                    || cache_size || '|' || order_flag AS fp FROM user_sequences) q
         ON o.object_type = 'SEQUENCE' AND q.sequence_name = o.object_name
  LEFT JOIN (SELECT synonym_name, table_owner || '.' || table_name || '@' || db_link AS fp FROM user_synonyms) y
         ON o.object_type = 'SYNONYM' AND y.synonym_name = o.object_name
 WHERE o.object_type NOT IN ('TABLE', 'INDEX', 'VIEW', 'LOB', 'NESTED TABLE', 'TABLE PARTITION', 'TABLE SUBPARTITION',
                             'INDEX PARTITION', 'INDEX SUBPARTITION', 'LOB PARTITION', 'LOB SUBPARTITION')
   AND o.generated = 'N' AND o.object_name NOT LIKE 'BIN$%'
   AND o.object_name NOT LIKE 'ISEQ$$\_%' ESCAPE '\';
"""
        return self._rows(cid, db, sql).get("O", [])

    def _column(self, c):
        default = (c.get("default") or "").strip() or None
        identity = {"ALWAYS": "a", "BY DEFAULT": "d"}.get(c.get("identity"))
        if identity:
            default = None
        return {"name": c["name"], "ordinal": c["pos"], "type": type_text(c), "nullable": c["nullable"] == "Y",
                "identity": identity, "generated": "virtual" if c["virtual"] == "YES" else None, "default": default,
                "owned_sequence": None, "collation": None, "comment": c.get("comment")}

    def _constraint_def(self, item, k):
        cols = ", ".join(q(c) for c in item["columns"])
        tail = "" if k.get("status") != "DISABLED" else " DISABLE"
        tail += " NOVALIDATE" if k.get("validated") == "NOT VALIDATED" and k.get("status") != "DISABLED" else ""
        if item["kind"] == "p":
            return f"PRIMARY KEY ({cols}){tail}"
        if item["kind"] == "u":
            return f"UNIQUE ({cols}){tail}"
        if item["kind"] == "c":
            return f"CHECK ({(k.get('cond') or '').strip()}){tail}"
        s = f"FOREIGN KEY ({cols}) REFERENCES {q(item['ref_table'] or '?')}({', '.join(q(c) for c in item['ref_columns'] or [])})"
        if k.get("del") and k["del"] != "NO ACTION":
            s += f" ON DELETE {k['del']}"
        return s + tail

    def _attach_ddl(self, cid, db, doc):
        """DBMS_METADATA 로 테이블 정의를 받고, 인덱스·코멘트 문을 덧붙인다."""
        if not doc["tables"]:
            return
        names = ", ".join("'" + t["name"].replace("'", "''") + "'" for t in doc["tables"])
        sql = f"""
EXEC DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SEGMENT_ATTRIBUTES', FALSE);
EXEC DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'STORAGE', FALSE);
EXEC DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SQLTERMINATOR', TRUE);
EXEC DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'EMIT_SCHEMA', FALSE);
SELECT 'D' || CHR(9) || JSON_OBJECT('name' VALUE table_name, 'ddl' VALUE DBMS_METADATA.GET_DDL('TABLE', table_name) RETURNING CLOB)
  FROM user_tables WHERE table_name IN ({names});
"""
        ddl = {d["name"]: d["ddl"] for d in self._rows(cid, db, sql).get("D", [])}
        for t in doc["tables"]:
            text = (ddl.get(t["name"]) or "").replace("\t", "    ")
            parts = ["\n".join(l.rstrip() for l in text.strip().splitlines() if l.strip())]
            parts += [i["definition"] + ";" for i in t["indexes"]]
            if t.get("comment"):
                parts.append(f"COMMENT ON TABLE {q(t['name'])} IS '{t['comment'].replace(chr(39), chr(39) * 2)}';")
            for c in t["columns"]:
                if c.get("comment"):
                    parts.append(f"COMMENT ON COLUMN {q(t['name'])}.{q(c['name'])} IS '{c['comment'].replace(chr(39), chr(39) * 2)}';")
            t["ddl"] = "\n".join(p for p in parts if p)

    def add_ddl(self, doc):
        """뷰 ddl 과 실행 순서 객체 목록(schema)을 채운다. FK 가 가리키는 테이블을 먼저 둔다."""
        for v in doc["views"]:
            v["ddl"] = f"CREATE OR REPLACE VIEW {q(v['name'])} AS\n{v['definition'].rstrip().rstrip(';')};"
        tables = {t["name"]: t for t in doc["tables"]}
        order, done, pending = [], set(), sorted(tables)
        while pending:
            ready = [n for n in pending if all(k["ref_table"] in done or k["ref_table"] == n or k["ref_table"] not in tables
                                              for k in tables[n]["constraints"] if k["kind"] == "f")] or pending[:1]
            for n in ready:
                order.append(n); done.add(n); pending.remove(n)
        doc["schema"] = ([{"kind": "table", "name": n, "text": tables[n].get("ddl", "")} for n in order]
                         + [{"kind": "view", "name": v["name"], "text": v["ddl"]} for v in doc["views"]])
        return doc
