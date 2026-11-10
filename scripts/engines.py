"""재생에 쓰는 DB 엔진. 엔진마다 컨테이너 기동, DB 생성, SQL 적용, 카탈로그 스냅샷, DDL 생성 방법이 다르다.

스냅샷은 엔진과 무관한 같은 모양이다(tables, views, types, schema). 문서에 engine 필드로 어느 엔진에서 떴는지 남긴다.
고정 식별자(oid·attnum)를 주는 엔진은 stable_ids 가 True 이고, 아니면 재생 쪽이 idmap 으로 id 를 이어 붙인다.

스냅샷에 담기지 않는 객체(트리거, 함수, 프로시저, 패키지, 시퀀스, 시노님 등)는 엔진의 objects() 가 목록으로 뜨고,
diff_objects() 가 단계 전후 목록을 비교해 added·dropped·changed·invalid 로 나눈다.
objects() 의 항목은 {type, name, table, status, fp} 이다. fp 는 정의 지문이고, 없으면 changed 판정에서 빠진다.
"""
import json

from i18n import L
from snapshot import catalog_sql, psql, remove_container, run, run_step, start_container


def no_objects():
    """변경이 없는 객체 변화 dict."""
    return {"added": [], "dropped": [], "changed": [], "invalid": []}


def diff_objects(before, after):
    """객체 목록 두 개를 (type, name) 으로 비교한다. 지문(fp)이 둘 다 있고 다르면 changed 다.

    같은 (type, name) 이 양쪽에 한 개씩이면 같은 객체로 본다. 딸린 테이블이 달라졌으면(테이블 이름 변경 등)
    changed 항목에 table_before(이전 테이블)를 붙인다. 같은 이름이 테이블마다 있는 경우(PostgreSQL 트리거)는
    테이블까지 같아야 같은 객체다. invalid 는 이번에 새로 INVALID 가 된 객체(전에 INVALID 였던 것은 뺀다).
    """
    pub = lambda o: {k: o.get(k) for k in ("type", "name", "table", "status")}
    order = lambda xs: sorted(xs, key=lambda o: (o["type"], o["name"], o["table"] or ""))
    groups = {}
    for side, items in ((0, before), (1, after)):
        for o in items:
            groups.setdefault((o["type"], o["name"]), ([], []))[side].append(o)
    added, dropped, changed, invalid = [], [], [], []
    for bs, as_ in groups.values():
        if len(bs) == 1 and len(as_) == 1:
            pairs = [(bs[0], as_[0])]
        else:
            by_table = {o.get("table"): o for o in bs}
            pairs = [(by_table[o.get("table")], o) for o in as_ if o.get("table") in by_table]
        old, new = {id(p[0]) for p in pairs}, {id(p[1]) for p in pairs}
        dropped += [pub(o) for o in bs if id(o) not in old]
        added += [pub(o) for o in as_ if id(o) not in new]
        for b, a in pairs:
            if a.get("fp") and b.get("fp") and a["fp"] != b["fp"]:
                row = pub(a)
                if b.get("table") != a.get("table"):
                    row["table_before"] = b.get("table")
                changed.append(row)
        prev = {id(a): b for b, a in pairs}
        invalid += [pub(a) for a in as_ if a.get("status") == "INVALID" and (id(a) not in prev or prev[id(a)].get("status") != "INVALID")]
    return {"added": order(added), "dropped": order(dropped), "changed": order(changed), "invalid": order(invalid)}


class Postgres:
    name = "postgres"
    label = "PostgreSQL"
    default_image = "postgres:16-alpine"
    default_schema = "public"
    dialect = "postgres"
    stable_ids = True
    transactional_ddl = True
    has_roles = True
    has_extensions = True

    def start(self, image):
        return start_container(image)

    def stop(self, cid):
        remove_container(cid)

    def db_for(self, schema):
        """재생에 쓸 DB 이름. PostgreSQL 은 DB 하나 안의 스키마를 보므로 DB 이름은 고정이다."""
        return "replay"

    def create_db(self, cid, db, schema=None):
        run(["docker", "exec", cid, "createdb", "-U", "postgres", db])

    def drop_db(self, cid, db, schema=None):
        run(["docker", "exec", cid, "dropdb", "-U", "postgres", "--if-exists", db])

    def apply(self, cid, db, sql, single_tx=True, schema=None):
        """SQL 을 적용한다. @return CompletedProcess(returncode, stderr) @throws StepTimeout 제한 시간 초과"""
        cmd = ["docker", "exec", "-i", cid, "psql", "-X", "-U", "postgres", "-d", db,
               "-v", "ON_ERROR_STOP=1", "-q"] + (["-1"] if single_tx else []) + ["-f", "-"]
        return run_step(cmd, sql)

    def query(self, cid, db, sql):
        return psql(cid, db, sql)

    def catalog(self, cid, db, schema):
        doc = json.loads(psql(cid, db, catalog_sql(schema)))
        doc["schema_name"] = schema
        return doc

    def objects(self, cid, db, schema):
        """스키마 안의 함수·프로시저·트리거·시퀀스·구체화 뷰. 컬럼에 딸린 시퀀스, 확장 소유 객체, 내부 트리거는 뺀다.

        지문은 pg_get_functiondef·pg_get_triggerdef·pg_get_viewdef 의 md5, 시퀀스는 pg_sequence 속성의 md5 다.
        """
        lit = "'" + schema.replace("'", "''") + "'"
        owned = "EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = '{cls}'::regclass AND d.objid = {oid} AND d.deptype = 'e')"
        sql = f"""
SELECT COALESCE(json_agg(o ORDER BY o.type, o.name, o."table"), '[]'::json) FROM (
  SELECT CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS type,
         p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS name,
         NULL::text AS "table", NULL::text AS status, md5(pg_get_functiondef(p.oid)) AS fp
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = {lit} AND p.prokind IN ('f', 'p') AND NOT {owned.format(cls='pg_proc', oid='p.oid')}
  UNION ALL
  SELECT 'TRIGGER', t.tgname, c.relname, NULL, md5(pg_get_triggerdef(t.oid))
    FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = {lit} AND NOT t.tgisinternal AND COALESCE(to_jsonb(t) ->> 'tgparentid', '0') = '0'
     AND NOT {owned.format(cls='pg_trigger', oid='t.oid')}
  UNION ALL
  SELECT 'SEQUENCE', c.relname, NULL, NULL,
         md5(concat_ws('|', s.seqtypid::regtype, s.seqstart, s.seqincrement, s.seqmax, s.seqmin, s.seqcache, s.seqcycle))
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_sequence s ON s.seqrelid = c.oid
   WHERE n.nspname = {lit} AND c.relkind = 'S' AND NOT {owned.format(cls='pg_class', oid='c.oid')}
     AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                        AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i'))
  UNION ALL
  SELECT 'MATERIALIZED VIEW', c.relname, NULL, NULL, md5(pg_get_viewdef(c.oid))
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = {lit} AND c.relkind = 'm' AND NOT {owned.format(cls='pg_class', oid='c.oid')}
) o;
"""
        return json.loads(psql(cid, db, sql))

    def add_ddl(self, doc):
        from ddl_postgres import add_ddl
        return add_ddl(doc)

    def prepare_sql(self, sql):
        """적용 전에 스크립트를 고친다. @return (SQL, 경고 목록)"""
        return sql, []

    def apply_warnings(self, proc):
        """적용은 성공했지만 알려야 할 출력. @return 경고 목록"""
        return []

    def error_text(self, stderr):
        return "\n".join(l for l in stderr.splitlines() if "NOTICE:" not in l).strip()


def get(name):
    """엔진 이름(postgres, mysql, mariadb, oracle)으로 엔진 객체를 만든다."""
    name = (name or "postgres").lower()
    if name in ("postgres", "postgresql", "pg"):
        return Postgres()
    if name in ("mysql", "mariadb"):
        from engine_mysql import MySQL
        return MySQL(name)
    if name == "oracle":
        from engine_oracle import Oracle
        return Oracle()
    raise SystemExit(L(f"모르는 DB: {name} (지원: postgres, mysql, mariadb, oracle)", f"Unknown database: {name} (supported: postgres, mysql, mariadb, oracle)"))


NAMES = ("postgres", "mysql", "mariadb", "oracle")


def add_ddl(doc):
    """스냅샷을 뜬 엔진에 맞게 ddl·schema 를 채운다."""
    return get(doc.get("engine", "postgres")).add_ddl(doc)

