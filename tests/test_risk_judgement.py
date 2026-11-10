"""위험 판정 검증: 타입 축소는 데이터 유실이 아니라 적용 실패 위험이고, 숫자 계열 정밀도 감소도 축소로 본다.

비교 화면 판정(collectRisks)은 헤드리스 Chrome 과 node 가 있을 때만 확인한다.
"""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from diff import diff  # noqa: E402
from render import embed, template  # noqa: E402
from summary_input import line  # noqa: E402
from timeline import step_data_loss, type_narrows  # noqa: E402

CHROME = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"]


class TypeNarrowsTest(unittest.TestCase):
    def test_sized_character_types(self):
        for eng in ("postgres", "mysql", "mariadb", "oracle"):
            self.assertEqual(type_narrows("VARCHAR2(30)", "VARCHAR2(20)", eng), "fail", eng)
            self.assertIsNone(type_narrows("VARCHAR2(20)", "VARCHAR2(30)", eng))
            self.assertIsNone(type_narrows("VARCHAR2(20)", "VARCHAR2(20)", eng))

    def test_scale_drop_rounds_except_oracle(self):
        for eng in ("postgres", "mysql", "mariadb"):
            self.assertEqual(type_narrows("numeric(10,4)", "numeric(10,2)", eng), "round", eng)
            self.assertEqual(type_narrows("decimal(10,2)", "decimal(10,0)", eng), "round", eng)
        self.assertEqual(type_narrows("NUMBER(10,2)", "NUMBER(10,1)", "oracle"), "fail")

    def test_integer_digits_and_precision_fail(self):
        for eng in ("postgres", "mysql", "oracle"):
            self.assertEqual(type_narrows("NUMBER(10,2)", "NUMBER(8,2)", eng), "fail", eng)
            self.assertEqual(type_narrows("NUMBER(10)", "NUMBER(6)", eng), "fail", eng)
            self.assertEqual(type_narrows("FLOAT(126)", "FLOAT(63)", eng), "fail", eng)
            self.assertIsNone(type_narrows("NUMBER(8,2)", "NUMBER(10,2)", eng))
            self.assertIsNone(type_narrows("NUMBER(10,2)", "NUMBER(12,4)", eng))

    def test_fractional_seconds(self):
        self.assertEqual(type_narrows("timestamp(6) without time zone", "timestamp(0) without time zone", "postgres"), "round")
        self.assertEqual(type_narrows("timestamp without time zone", "timestamp(3) without time zone", "postgres"), "round")
        self.assertIsNone(type_narrows("timestamp(3) with time zone", "timestamp with time zone", "postgres"))
        self.assertEqual(type_narrows("datetime(6)", "datetime", "mysql"), "round")
        self.assertEqual(type_narrows("time(6)", "time(3)", "mariadb"), "round")
        self.assertIsNone(type_narrows("datetime", "datetime(6)", "mysql"))
        self.assertEqual(type_narrows("TIMESTAMP(6)", "TIMESTAMP(3)", "oracle"), "fail")

    def test_round_is_data_loss_fail_is_not(self):
        self.assertFalse(step_data_loss([{"kind": "column_type", "before": "VARCHAR2(30)", "after": "VARCHAR2(20)"}]))
        self.assertTrue(step_data_loss([{"kind": "column_type", "before": "numeric(10,4)", "after": "numeric(10,2)"}], "postgres"))
        self.assertFalse(step_data_loss([{"kind": "column_type", "before": "NUMBER(10,4)", "after": "NUMBER(10,2)"}], "oracle"))
        self.assertTrue(step_data_loss([{"kind": "column_dropped"}]))
        self.assertTrue(step_data_loss([{"kind": "table_recreated"}]))


class SummaryInputTest(unittest.TestCase):
    def test_round_is_marked_as_data_loss_and_fail_as_narrowed(self):
        ev = lambda b, a: {"kind": "column_type", "table": "t", "column": "c", "before": b, "after": a}
        self.assertIn("데이터 유실", line(ev("numeric(10,4)", "numeric(10,2)"), "postgres"))
        self.assertIn("크기 축소", line(ev("numeric(10,4)", "numeric(10,2)"), "oracle"))
        self.assertIn("크기 축소", line(ev("varchar(30)", "varchar(20)"), "postgres"))
        self.assertNotIn("축소", line(ev("varchar(20)", "varchar(30)"), "postgres"))


def col(name, ty, n, nullable=True, default=None):
    return {"name": name, "ordinal": n, "type": ty, "nullable": nullable, "default": default, "comment": None, "identity": None}


def table(oid, name, cols, cons=(), idx=()):
    return {"name": name, "oid": oid, "comment": None, "indexes": list(idx), "constraints": list(cons), "columns": cols}


def risks_of(doc):
    """doc 으로 비교 화면을 렌더하고 collectRisks() 결과(level, kind, what, why)를 돌려준다."""
    chrome = next((p for p in CHROME if Path(p).exists()), None)
    node = shutil.which("node")
    if not node or not chrome:
        raise unittest.SkipTest("node 또는 Chrome 이 없습니다")
    with tempfile.TemporaryDirectory() as d:
        page = Path(d) / "compare.html"
        page.write_text(embed(template("compare.html").replace('<html lang="ko">', '<html lang="en">', 1), doc))
        r = subprocess.run([node, str(ROOT / "tests" / "cdp_eval.mjs"), str(page),
                            "JSON.stringify({risks: collectRisks().map(r => ({level: r.level, kind: r.kind || null, what: r.what, why: r.why})),"
                            " headline: document.getElementById('headline').textContent,"
                            " kpi: [...document.querySelectorAll('#stats .kpi')].map(k => k.innerText.replace(/\\s+/g, ' ').trim())})"],
                           capture_output=True, text=True, timeout=90, env={**os.environ, "CHROME_PATH": chrome})
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(json.loads(r.stdout))


def compare_doc(before, after, **extra):
    b = {"tables": before, "views": [], "replay_id": "r"}
    a = {"tables": after, "views": [], "replay_id": "r"}
    d = diff(b, a)
    d["before"]["source"], d["after"]["source"] = "V1", "V2"
    d.update(extra, embed=True, lang="en", generated_at="t")
    return d


class RiskDisplayTest(unittest.TestCase):
    def test_shrink_is_attention_not_loss(self):
        b = [table(1, "customers", [col("id", "NUMBER", 1), col("phone", "VARCHAR2(30)", 2)])]
        a = [table(1, "customers", [col("id", "NUMBER", 1), col("phone", "VARCHAR2(20)", 2)])]
        r = risks_of(compare_doc(b, a))["risks"]
        self.assertEqual([(x["level"], x["what"]) for x in r], [("attention", "customers.phone")])
        self.assertIn("Fails if any existing value is longer", r[0]["why"])

    def test_scale_drop_is_danger_on_postgres_attention_on_oracle(self):
        b = [table(1, "t", [col("a", "numeric(10,4)", 1)])]
        a = [table(1, "t", [col("a", "numeric(10,2)", 1)])]
        r = risks_of(compare_doc(b, a, engine="postgres"))["risks"]
        self.assertEqual([(x["level"], x["kind"], x["what"]) for x in r], [("danger", "round", "t.a")])
        self.assertIn("Lowers decimal places numeric(10,4) → numeric(10,2). Existing values are rounded.", r[0]["why"])
        b = [table(1, "t", [col("a", "NUMBER(10,4)", 1)])]
        a = [table(1, "t", [col("a", "NUMBER(10,2)", 1)])]
        r = risks_of(compare_doc(b, a, engine="oracle"))["risks"]
        self.assertEqual([(x["level"], x["kind"]) for x in r], [("attention", None)])

    def test_fractional_seconds_drop(self):
        b = [table(1, "t", [col("a", "timestamp(6) without time zone", 1)])]
        a = [table(1, "t", [col("a", "timestamp(0) without time zone", 1)])]
        r = risks_of(compare_doc(b, a, engine="postgres"))["risks"]
        self.assertEqual(sorted((x["level"], x["what"]) for x in r), [("danger", "t.a")])
        self.assertIn("lose sub-second precision", r[0]["why"])
        my = risks_of(compare_doc([table(1, "t", [col("a", "datetime(6)", 1)])], [table(1, "t", [col("a", "datetime", 1)])], engine="mysql"))["risks"]
        self.assertEqual([x["level"] for x in my], ["danger"])
        ora = risks_of(compare_doc([table(1, "t", [col("a", "TIMESTAMP(6)", 1)])], [table(1, "t", [col("a", "TIMESTAMP(3)", 1)])], engine="oracle"))["risks"]
        self.assertEqual([x["level"] for x in ora], ["attention"])

    def test_number_precision_decrease_is_flagged(self):
        b = [table(1, "t", [col("a", "NUMBER(10,2)", 1), col("b", "decimal(10,2)", 2), col("c", "NUMBER(10,2)", 3)])]
        a = [table(1, "t", [col("a", "NUMBER(8,2)", 1), col("b", "decimal(8,2)", 2), col("c", "NUMBER(12,2)", 3)])]
        self.assertEqual(sorted(x["what"] for x in risks_of(compare_doc(b, a))["risks"]), ["t.a", "t.b"])

    def test_range_step_not_counted_twice(self):
        b = [table(1, "a", [col("id", "int", 1)]), table(2, "b", [col("id", "int", 1), col("x", "int", 2)])]
        a = [table(2, "b", [col("id", "int", 1)])]
        events = [{"kind": "table_dropped", "table": "a", "column": None}, {"kind": "column_dropped", "table": "b", "column": "x"}]
        steps = [{"step": 1, "label": "V1", "data_loss": True, "event_count": 1, "events": events[:1]},
                 {"step": 2, "label": "V2", "data_loss": True, "event_count": 1, "events": events[1:]},
                 {"step": 3, "label": "V3", "data_loss": True, "event_count": 1, "events": [{"kind": "table_dropped", "table": "tmp", "column": None}]}]
        r = risks_of(compare_doc(b, a, range_steps=steps))["risks"]
        self.assertEqual(sorted((str(x["kind"]), x["what"]) for x in r), [("None", "a"), ("None", "b.x"), ("step", "V3")])

    def test_recreated_table_kpi(self):
        b = [table(1, "t", [col("id", "int", 1)])]
        a = [table(2, "t", [col("id", "int", 1)])]
        d = compare_doc(b, a)
        kpi = risks_of(d)["kpi"]
        self.assertTrue(any(k.startswith("Tables") and "Recreated 1" in k and "Changed" not in k for k in kpi), kpi)

    def test_constraint_on_column_added_in_same_step_is_not_flagged(self):
        fk = {"name": "fk", "kind": "f", "columns": ["m"], "ref_table": "pm", "ref_columns": ["id"], "definition": "FOREIGN KEY (m) REFERENCES pm(id)"}
        uq = {"name": "uq", "unique": True, "columns": ["sku"], "definition": "CREATE UNIQUE INDEX uq ON t USING btree (sku)"}
        pm = table(2, "pm", [col("id", "int", 1)])
        b = [table(1, "t", [col("id", "int", 1), col("email", "text", 2)]), pm]
        a = [table(1, "t", [col("id", "int", 1), col("email", "text", 2), col("m", "int", 3), col("sku", "text", 4)], [fk], [uq]), pm]
        self.assertEqual(risks_of(compare_doc(b, a))["risks"], [])
        b2 = [table(1, "t", [col("id", "int", 1), col("m", "int", 3)]), pm]
        a2 = [table(1, "t", [col("id", "int", 1), col("m", "int", 3)], [fk]), pm]
        self.assertEqual([x["what"] for x in risks_of(compare_doc(b2, a2))["risks"]], ["t FK"])

    def test_objects_and_range_steps(self):
        t = [table(1, "orders", [col("id", "int", 1)])]
        objs = {"added": [{"type": "TRIGGER", "name": "TRG_X", "table": "ORDERS", "status": "VALID"}], "dropped": [],
                "changed": [], "invalid": [{"type": "FUNCTION", "name": "FN_Y", "table": None, "status": "INVALID"}]}
        steps = [{"step": 1, "label": "V1", "data_loss": False, "event_count": 1}, {"step": 2, "label": "V2", "data_loss": True, "event_count": 1}]
        r = risks_of(compare_doc(t, t, objects=objs, range_steps=steps))["risks"]
        self.assertEqual([(x["level"], x["kind"], x["what"]) for x in r],
                         [("danger", "step", "V2"), ("danger", "invalid", "FN_Y"), ("attention", None, "TRG_X")])
        self.assertIn("Trigger TRG_X added on ORDERS", r[2]["why"])

    def test_missing_objects_is_fine(self):
        t = [table(1, "orders", [col("id", "int", 1)])]
        self.assertEqual(risks_of(compare_doc(t, t))["risks"], [])


if __name__ == "__main__":
    unittest.main()
