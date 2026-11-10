"""잠금 힌트(computeHints)·확인 쿼리(check, checkScript)·식별자 인용(quoteIdent) 규칙 검증. docker 없이 node 로 templates/risks.js 를 부른다."""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RISKS = ROOT / "templates" / "risks.js"


def js(expr):
    """risks.js 를 불러 expr 의 값을 JSON 으로 돌려받는다. expr 안에서 R 이 모듈이다."""
    code = f"const R = require({json.dumps(str(RISKS))}); process.stdout.write(JSON.stringify({expr}));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def col(name, type_, nullable=True, status="same", before=None, changes=(), **kw):
    after = {"name": name, "type": type_, "nullable": nullable, "default": None, "identity": None, "generated": None, "comment": None, **kw}
    b = before if before is not None else (None if status == "added" else dict(after))
    return {"name": name, "status": status, "before": b, "after": None if status == "dropped" else after, "changes": list(changes)}


def table(name, columns, constraints=(), indexes=(), status="changed", **kw):
    return {"name": name, "status": status, "columns": columns, "constraints": list(constraints), "indexes": list(indexes),
            "comment": {"before": None, "after": None}, **kw}


def doc(tables, engine="postgres", sql=None, **kw):
    return {"tables": tables, "views": [], "events": [], "engine": engine, "sql": sql, **kw}


@unittest.skipUnless(shutil.which("node"), "node 필요")
class QuoteTest(unittest.TestCase):
    def test_quotes_only_when_needed(self):
        got = js("[['order','postgres'],['Name','postgres'],['name','postgres'],['LEVEL','oracle'],['NAME','oracle'],['name','oracle'],['x','mysql']]"
                 ".map(([n, e]) => R.quoteIdent(n, e))")
        self.assertEqual(got, ['"order"', '"Name"', 'name', '"LEVEL"', 'NAME', '"name"', '`x`'])

    def test_pg_rewrite_rules(self):
        cases = [["character varying(10)", "character varying(20)", True], ["character varying(10)", "text", True],
                 ["numeric(10,2)", "numeric(12,2)", True], ["timestamp(3) without time zone", "timestamp(6) without time zone", True],
                 ["integer", "bigint", False], ["character(5)", "character(8)", False], ["numeric(10,4)", "numeric(10,2)", False]]
        got = js(f"{json.dumps(cases)}.map(([b, a]) => R.pgNoRewrite(b, a))")
        self.assertEqual(got, [c[2] for c in cases])


@unittest.skipUnless(shutil.which("node"), "node 필요")
class HintTest(unittest.TestCase):
    def hints(self, d):
        return js(f"R.computeHints({json.dumps(d)}, {{lang: 'en'}})")

    def idx_doc(self, sql):
        t = table("orders", [col("id", "integer")], indexes=[{"name": "ix_orders_id", "status": "added", "unique": False, "columns": ["id"],
                                                              "definition": "CREATE INDEX ix_orders_id ON public.orders USING btree (id)"}])
        return doc([t], sql=sql)

    def test_concurrently_suppresses_index_hint(self):
        self.assertEqual(self.hints(self.idx_doc("CREATE INDEX CONCURRENTLY ix_orders_id ON orders (id);")), [])

    def test_plain_index_gets_definite_hint(self):
        h = self.hints(self.idx_doc("create index ix_orders_id on orders (id);"))
        self.assertEqual([x["lead"] for x in h], ["Blocks writes"])
        self.assertTrue(h[0]["why"].startswith("CREATE INDEX blocks"))

    def test_drop_index_concurrently_does_not_hide_plain_create(self):
        h = self.hints(self.idx_doc("DROP INDEX CONCURRENTLY ix_orders_id; CREATE INDEX ix_orders_id ON orders (id);"))
        self.assertEqual(len(h), 1)

    def test_oracle_definition_novalidate_skips_hint(self):
        for d in ("CHECK (A > 0) NOVALIDATE", "CHECK (A > 0) DISABLE"):
            k = {"name": "CK", "kind": "c", "status": "added", "columns": ["A"], "definition": d}
            self.assertEqual(self.hints(doc([table("T", [col("A", "NUMBER")], constraints=[k])], engine="oracle", sql=None)), [])

    def test_mixed_unnamed_oracle_fks_still_hint(self):
        fks = [{"name": f"SYS_C{n}", "kind": "f", "status": "added", "columns": [c], "ref_table": "P", "ref_columns": ["ID"], "definition": ""}
               for n, c in ((1, "A"), (2, "B"))]
        t = table("C", [col("A", "NUMBER"), col("B", "NUMBER")], constraints=fks)
        sql = "ALTER TABLE c ADD FOREIGN KEY (a) REFERENCES p(id) ENABLE NOVALIDATE; ALTER TABLE c ADD FOREIGN KEY (b) REFERENCES p(id);"
        self.assertEqual(len(self.hints(doc([t], engine="oracle", sql=sql))), 2)

    def test_stable_default_and_interval_growth_do_not_rewrite(self):
        add = col("d", "timestamp with time zone", status="added", default="statement_timestamp()")
        grow = col("i", "interval(3)", before={"name": "i", "type": "interval(1)", "nullable": True}, changes=["type"])
        self.assertEqual(self.hints(doc([table("t", [add, grow])], sql="")), [])

    def test_mysql_varchar_prefix_boundary(self):
        got = js("[['varchar(50)','varchar(100)'],['varchar(200)','varchar(300)'],['char(5)','char(10)'],"
                 "[\"enum('a','b')\",\"enum('a','b','c')\"]].map(([b, a]) => R.mysqlInPlace(b, a))")
        self.assertEqual(got, [False, True, False, True])

    def test_unknown_sql_gets_conditional_hint(self):
        h = self.hints(self.idx_doc(None))
        self.assertTrue(h[0]["why"].startswith("Without CONCURRENTLY"))

    def test_new_table_gets_no_hints(self):
        t = table("orders", [col("id", "integer", status="added")], status="added",
                  indexes=[{"name": "ix", "status": "added", "unique": False, "columns": ["id"], "definition": ""}])
        self.assertEqual(self.hints(doc([t], sql="CREATE INDEX ix ON orders (id);")), [])

    def test_not_valid_fk_gets_no_hint(self):
        fk = {"name": "fk_a", "kind": "f", "status": "added", "columns": ["a"], "ref_table": "p", "ref_columns": ["id"],
              "definition": "FOREIGN KEY (a) REFERENCES p(id) NOT VALID"}
        self.assertEqual(self.hints(doc([table("c", [col("a", "integer")], constraints=[fk])], sql="")), [])

    def test_oracle_engine_named_constraint_found_by_table(self):
        fk = {"name": "SYS_C0099", "kind": "f", "status": "added", "columns": ["A"], "ref_table": "P", "ref_columns": ["ID"], "definition": ""}
        t = table("C", [col("A", "NUMBER")], constraints=[fk])
        self.assertEqual(self.hints(doc([t], engine="oracle", sql="ALTER TABLE c ADD FOREIGN KEY (a) REFERENCES p(id) ENABLE NOVALIDATE;")), [])
        self.assertEqual(len(self.hints(doc([t], engine="oracle", sql="ALTER TABLE c ADD FOREIGN KEY (a) REFERENCES p(id);"))), 1)

    def test_range_has_no_hints(self):
        self.assertEqual(self.hints({**self.idx_doc(None), "range_steps": []}), [])

    def test_pk_column_not_null_is_not_hinted_twice(self):
        pk = {"name": "t_pkey", "kind": "p", "status": "added", "columns": ["id"], "definition": "PRIMARY KEY (id)"}
        c = col("id", "integer", nullable=False, before={"name": "id", "type": "integer", "nullable": True}, changes=["nullable"])
        h = self.hints(doc([table("t", [c], constraints=[pk])], sql="ALTER TABLE t ADD PRIMARY KEY (id);"))
        self.assertEqual([x["what"] for x in h], ["t PK t_pkey"])


@unittest.skipUnless(shutil.which("node"), "node 필요")
class CheckTest(unittest.TestCase):
    def risks(self, d):
        return js(f"R.computeRisks({json.dumps(d)}, {{lang: 'en'}})")

    def test_oracle_number_shrink_needs_empty_column(self):
        c = col("AMOUNT", "NUMBER(8,2)", before={"name": "AMOUNT", "type": "NUMBER(10,2)", "nullable": True}, changes=["type"])
        r = self.risks(doc([table("T", [c])], engine="oracle"))[0]
        self.assertIn("empty column", r["why"])
        self.assertEqual(r["check"]["sql"], "SELECT COUNT(*) FROM T WHERE AMOUNT IS NOT NULL;")

    def test_renamed_column_uses_old_name(self):
        c = col("memo", "text", nullable=False, before={"name": "note", "type": "text", "nullable": True}, changes=["nullable"], status="renamed")
        c["confidence"] = "confirmed"
        r = self.risks(doc([table("t", [c])]))[0]
        self.assertEqual(r["check"]["sql"], "SELECT COUNT(*) FROM t WHERE note IS NULL;")

    def test_constraint_on_column_added_in_same_step_has_no_check(self):
        k = {"name": "ck", "kind": "c", "status": "added", "columns": ["x"], "definition": "CHECK ((x > 0))"}
        r = self.risks(doc([table("t", [col("x", "integer", status="added", default="1")], constraints=[k])]))
        self.assertEqual([x.get("check") for x in r if x["what"] == "t CHECK"], [None])

    def test_check_suffix_and_odd_unique_indexes(self):
        self.assertEqual(js("['CHECK ((x > 0)) NOT VALID', 'CHECK (A > 0) NOVALIDATE'].map(R.checkExpr)"), ["x > 0", "A > 0"])
        got = js("[{definition: 'CREATE UNIQUE INDEX u ON t USING btree (e) WHERE (d IS NULL)', columns: ['e']},"
                 " {definition: 'CREATE UNIQUE INDEX u ON t USING btree (a, lower(b))', columns: ['a']}, {columns: ['EMAIL DESC']}, {columns: ['UPPER(EMAIL)']}]"
                 ".map(R.plainIndexColumns)")
        self.assertEqual(got, [None, None, ["EMAIL"], None])

    def test_script_comment_falls_back_to_first_sentence(self):
        c = col("name", "character varying(20)", before={"name": "name", "type": "character varying(50)", "nullable": True}, changes=["type"])
        s = js(f"R.checkScript(R.computeRisks({json.dumps(doc([table('t', [c])]))}, {{lang: 'en'}}), {{lang: 'en'}})")
        self.assertEqual(s.splitlines()[0], "-- 1. t.name · Shrinks varchar(50) → varchar(20).")
        self.assertIn("char_length(name) > 20", s)


if __name__ == "__main__":
    unittest.main()
