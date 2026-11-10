"""스냅샷 → CREATE 문 생성(ddl_postgres.py) 검증. docker 가 있으면 생성한 DDL 을 새 DB 에 적용해 왕복 비교한다."""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from ddl_postgres import add_ddl, default_text, ident, schema_objects, table_ddl, type_text  # noqa: E402
from diff import diff  # noqa: E402


def col(name, type_, nullable=True, default=None, identity=None, comment=None, **kw):
    return dict({"name": name, "type": type_, "nullable": nullable, "default": default, "identity": identity,
                 "comment": comment}, **kw)


def con(name, kind, columns, definition, ref_table=None, ref_columns=None):
    return {"name": name, "kind": kind, "columns": columns, "definition": definition,
            "ref_table": ref_table, "ref_columns": ref_columns}


def table(name, columns, constraints=(), indexes=(), comment=None):
    return {"name": name, "comment": comment, "columns": list(columns), "constraints": list(constraints), "indexes": list(indexes)}


class DdlTest(unittest.TestCase):
    def test_users_example(self):
        t = table("users", [
            col("id", "integer", False, "nextval('users_id_seq'::regclass)", owned_sequence="public.users_id_seq"),
            col("username", "character varying(50)", False),
            col("created_at", "timestamp without time zone", True, "CURRENT_TIMESTAMP"),
            col("is_active", "boolean", True, "true"),
            col("status", "character varying(20)", False, "'ACTIVE'::character varying"),
        ], [con("users_pkey", "p", ["id"], "PRIMARY KEY (id)"), con("users_username_key", "u", ["username"], "UNIQUE (username)")])
        self.assertEqual(table_ddl(t), "CREATE TABLE users (\n"
                         "    id SERIAL PRIMARY KEY,\n"
                         "    username VARCHAR(50) NOT NULL UNIQUE,\n"
                         "    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n"
                         "    is_active BOOLEAN DEFAULT true,\n"
                         "    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'\n"
                         ");")

    def test_renamed_table_keeps_line_shape(self):
        t = table("order_lines", [
            col("id", "bigint", False, "nextval('order_items_id_seq'::regclass)", owned_sequence="public.order_items_id_seq"),
            col("order_id", "bigint", False)],
            [con("order_items_pkey", "p", ["id"], "PRIMARY KEY (id)"),
             con("order_items_order_id_fkey", "f", ["order_id"], "FOREIGN KEY (order_id) REFERENCES orders(id)", "orders", ["id"])])
        self.assertEqual(table_ddl(t), "CREATE TABLE order_lines (\n"
                         "    id BIGSERIAL CONSTRAINT order_items_pkey PRIMARY KEY,\n"
                         "    order_id BIGINT NOT NULL CONSTRAINT order_items_order_id_fkey REFERENCES orders(id)\n"
                         ");")

    def test_unowned_sequence_is_created(self):
        t = table("t", [col("id", "bigint", False, "nextval('shared_seq'::regclass)")])
        self.assertTrue(table_ddl(t).startswith("CREATE SEQUENCE IF NOT EXISTS shared_seq;\nCREATE TABLE t (\n    id BIGINT NOT NULL DEFAULT nextval('shared_seq'::regclass)"))

    def test_constraint_options_and_table_level_rules(self):
        t = table("stocks", [col("a", "integer", False), col("b", "integer", False, comment="it's"),
                             col("total", "numeric", True, "(a * b)", generated="s"),
                             col("left", "text", True, collation='"C"')],
                  [con("uq_ab", "u", ["a", "b"], "UNIQUE (a, b)"),
                   con("stocks_a_fkey", "f", ["a"], "FOREIGN KEY (a) REFERENCES w(id) ON DELETE CASCADE", "w", ["id"]),
                   con("stocks_b_fkey", "f", ["b"], "FOREIGN KEY (b) REFERENCES w(id) NOT VALID", "w", ["id"]),
                   con("stocks_b_key", "u", ["b"], "UNIQUE NULLS NOT DISTINCT (b) DEFERRABLE INITIALLY DEFERRED"),
                   con("stocks_b_check", "c", ["b"], "CHECK ((b > 0))")],
                  [{"definition": "CREATE INDEX ix ON public.stocks USING btree (b) WHERE (b > 1)"}])
        ddl = table_ddl(t)
        self.assertIn("    a INTEGER NOT NULL REFERENCES w(id) ON DELETE CASCADE,", ddl)
        self.assertIn("    b INTEGER NOT NULL UNIQUE NULLS NOT DISTINCT DEFERRABLE INITIALLY DEFERRED,", ddl)
        self.assertIn("    total NUMERIC GENERATED ALWAYS AS ((a * b)) STORED,", ddl)
        self.assertIn('    "left" TEXT COLLATE "C",', ddl)
        self.assertIn("    CONSTRAINT uq_ab UNIQUE (a, b)", ddl)
        self.assertIn("ALTER TABLE stocks ADD CONSTRAINT stocks_b_fkey FOREIGN KEY (b) REFERENCES w(id) NOT VALID;", ddl)
        self.assertIn("    CHECK ((b > 0))", ddl)
        self.assertIn("CREATE INDEX ix ON stocks (b) WHERE (b > 1);", ddl)
        self.assertIn("COMMENT ON COLUMN stocks.b IS 'it''s';", ddl)

    def test_index_options_stay_table_level(self):
        t = table("t1", [col("a", "integer"), col("b", "integer")],
                  [con("t1_a_key", "u", ["a"], "UNIQUE (a) INCLUDE (b)")])
        self.assertIn("    UNIQUE (a) INCLUDE (b)", table_ddl(t))
        self.assertIn("    a INTEGER,", table_ddl(t))

    def test_defaults_keep_expressions(self):
        self.assertEqual(default_text("'A'::character varying"), "'A'")
        self.assertEqual(default_text("('a'::text IS NOT NULL)"), "('a'::text IS NOT NULL)")
        self.assertEqual(default_text("('x'::text COLLATE \"C\")"), "('x'::text COLLATE \"C\")")

    def test_identity_and_quoting(self):
        t = table("Order", [col("user", "bigint", False, identity="d"), col("join", "text")])
        ddl = table_ddl(t)
        self.assertIn('CREATE TABLE "Order" (\n    "user" BIGINT GENERATED BY DEFAULT AS IDENTITY NOT NULL', ddl)
        self.assertIn('    "join" TEXT', ddl)
        self.assertEqual(ident('a"b'), '"a""b"')

    def test_type_names(self):
        self.assertEqual([type_text(x) for x in ["character varying(100)[]", "timestamp(3) with time zone", "my_enum", "jsonb"]],
                         ["VARCHAR(100)[]", "TIMESTAMPTZ(3)", "my_enum", "JSONB"])

    def test_schema_order_handles_cycles_and_view_dependencies(self):
        fk = lambda n, c, r: con(n, "f", [c], f"FOREIGN KEY ({c}) REFERENCES {r}(id)", r, ["id"])
        snap = {"tables": [table("a", [col("id", "integer", False), col("b_id", "integer")], [fk("a_b_id_fkey", "b_id", "b")]),
                           table("b", [col("id", "integer", False), col("a_id", "integer")], [fk("b_a_id_fkey", "a_id", "a")]),
                           table("c", [col("id", "integer", False), col("z_id", "integer")], [fk("c_z_id_fkey", "z_id", "z")]),
                           table("z", [col("id", "integer", False)])],
                "views": [{"name": "v_a", "definition": " SELECT * FROM v_c;", "depends": ["v_c"]},
                          {"name": "v_c", "definition": " SELECT 1;", "depends": []}]}
        objs = schema_objects(snap)
        names = [(o["kind"], o["name"]) for o in objs]
        self.assertLess(names.index(("table", "z")), names.index(("table", "c")))
        self.assertIn(("constraint", "a"), names)
        self.assertLess(names.index(("view", "v_c")), names.index(("view", "v_a")))
        self.assertTrue(all(not o["text"].endswith(";;") for o in objs))


def has_docker():
    return shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


EXOTIC = """
CREATE TABLE "Mixed Case" (id serial PRIMARY KEY, "left" text COLLATE "C", "join" int);
CREATE TABLE parent (id bigserial PRIMARY KEY, code varchar(10) NOT NULL UNIQUE DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE child (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_id bigint REFERENCES parent(id) ON DELETE SET NULL,
    qty int NOT NULL DEFAULT 1 CHECK (qty > 0),
    price numeric(10,2) NOT NULL DEFAULT '0'::numeric,
    total numeric GENERATED ALWAYS AS (price * qty) STORED,
    tags text[] DEFAULT '{}'::text[],
    note text DEFAULT ('a'::text || 'b'),
    CONSTRAINT child_uq UNIQUE NULLS NOT DISTINCT (parent_id, qty)
);
ALTER TABLE child ADD CONSTRAINT child_parent_fk2 FOREIGN KEY (parent_id) REFERENCES parent(id) NOT VALID;
CREATE INDEX child_note_lower ON child (lower(note)) WHERE note IS NOT NULL;
CREATE TABLE ping (id int PRIMARY KEY, pong_id int);
CREATE TABLE pong (id int PRIMARY KEY, ping_id int REFERENCES ping(id));
ALTER TABLE ping ADD FOREIGN KEY (pong_id) REFERENCES pong(id);
CREATE TABLE events (id int, at date NOT NULL) PARTITION BY RANGE (at);
CREATE TABLE old_name (id serial PRIMARY KEY, v text);
ALTER TABLE old_name RENAME TO new_name;
COMMENT ON TABLE parent IS 'it''s a parent';
CREATE VIEW v_base AS SELECT id, code FROM parent;
CREATE VIEW a_top AS SELECT * FROM v_base WHERE code <> 'x';
"""


@unittest.skipUnless(has_docker(), "docker 필요")
class RoundTripTest(unittest.TestCase):
    """DDL 을 적용 → 스냅샷 → 그 스냅샷의 schema 를 새 DB 에 적용 → 다시 스냅샷. 두 스냅샷의 diff 가 비어 있어야 한다."""

    def roundtrip(self, ddl, setup=""):
        from snapshot import CATALOG_SQL, psql, run, start_container
        cid = start_container("postgres:16-alpine")
        try:
            run(["docker", "exec", cid, "createdb", "-U", "postgres", "a"])
            run(["docker", "exec", cid, "createdb", "-U", "postgres", "b"])
            psql(cid, "a", setup + ddl)
            first = add_ddl(json.loads(psql(cid, "a", CATALOG_SQL)))
            psql(cid, "b", setup + "\n\n".join(o["text"] for o in first["schema"]))
            second = add_ddl(json.loads(psql(cid, "b", CATALOG_SQL)))
        finally:
            run(["docker", "stop", cid], check=False)
        return first, second

    def assert_same(self, ddl, setup=""):
        first, second = self.roundtrip(ddl, setup)
        d = diff(first, second)
        self.assertEqual([(e["kind"], e["table"], e.get("column")) for e in d["events"]], [])
        self.assertEqual([t["ddl"] for t in first["tables"]], [t["ddl"] for t in second["tables"]])

    def test_exotic_schema(self):
        self.assert_same(EXOTIC)

    def test_enum_and_fk_to_other_schema(self):
        first, _ = self.roundtrip("CREATE TYPE \"Role\" AS ENUM ('USER', 'it''s');\n"
                                  "CREATE TABLE member (id int PRIMARY KEY, role \"Role\" NOT NULL DEFAULT 'USER',"
                                  " auth_id uuid REFERENCES auth.users(id) ON DELETE CASCADE);",
                                  setup="CREATE SCHEMA auth; CREATE TABLE auth.users (id uuid PRIMARY KEY);\n")
        self.assertEqual(first["types"], [{"name": "Role", "kind": "enum", "labels": ["USER", "it's"]}])
        self.assertEqual(first["schema"][0]["text"], "CREATE TYPE \"Role\" AS ENUM ('USER', 'it''s');")
        fk = next(k for k in first["tables"][0]["constraints"] if k["kind"] == "f")
        self.assertEqual((fk["ref_table"], fk["ref_schema"]), ("auth.users", "auth"))
        self.assertIn("REFERENCES auth.users(id) ON DELETE CASCADE", first["tables"][0]["ddl"])
        self.assert_same("CREATE TYPE \"Role\" AS ENUM ('USER', 'it''s');\n"
                         "CREATE TABLE member (id int PRIMARY KEY, role \"Role\" NOT NULL DEFAULT 'USER',"
                         " auth_id uuid REFERENCES auth.users(id) ON DELETE CASCADE);",
                         setup="CREATE SCHEMA auth; CREATE TABLE auth.users (id uuid PRIMARY KEY);\n")

    def test_samples(self):
        self.assert_same((ROOT / "samples" / "large_v2.sql").read_text())
        flyway = "\n".join(p.read_text() for p in sorted((ROOT / "samples" / "flyway").glob("V*.sql"),
                                                          key=lambda p: [int(x) for x in p.name[1:].split("__")[0].replace("_", ".").split(".")]))
        self.assert_same("CREATE ROLE report_role;\n" + flyway)


if __name__ == "__main__":
    unittest.main()
