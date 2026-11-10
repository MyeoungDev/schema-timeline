"""스냅샷에 담기지 않는 객체(트리거·함수·프로시저·시퀀스 등)의 추가·삭제·변경·INVALID 추적 검증.

diff_objects 와 요약 입력은 docker 없이 돈다. 엔진별 재생은 그 엔진 이미지가 로컬에 있을 때만 돈다(받지 않는다).
"""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from engines import diff_objects, no_objects  # noqa: E402
from summary_input import object_lines  # noqa: E402
from test_engines import has_image, replay_dir  # noqa: E402


def obj(type_, name, table=None, status=None, fp=None):
    return {"type": type_, "name": name, "table": table, "status": status, "fp": fp}


def keys(items):
    return [(o["type"], o["name"], o["table"]) for o in items]


class DiffObjectsTest(unittest.TestCase):
    def test_added_dropped_changed_invalid(self):
        before = [obj("FUNCTION", "f()", fp="1"), obj("TRIGGER", "t", "a", fp="1"), obj("PROCEDURE", "p", status="INVALID", fp="1"),
                  obj("SEQUENCE", "s")]
        after = [obj("FUNCTION", "f()", fp="2"), obj("PROCEDURE", "p", status="INVALID", fp="1"), obj("SEQUENCE", "s"),
                 obj("PACKAGE", "pk", status="INVALID", fp="9")]
        d = diff_objects(before, after)
        self.assertEqual(keys(d["added"]), [("PACKAGE", "pk", None)])
        self.assertEqual(keys(d["dropped"]), [("TRIGGER", "t", "a")])
        self.assertEqual(keys(d["changed"]), [("FUNCTION", "f()", None)])
        self.assertEqual(keys(d["invalid"]), [("PACKAGE", "pk", None)])
        self.assertEqual(set(d["added"][0]), {"type", "name", "table", "status"})

    def test_missing_fingerprint_is_not_changed(self):
        d = diff_objects([obj("SEQUENCE", "s")], [obj("SEQUENCE", "s", fp="x")])
        self.assertEqual(d, no_objects())

    def test_table_rename_is_changed_not_drop_and_add(self):
        d = diff_objects([obj("TRIGGER", "t", "a", fp="1")], [obj("TRIGGER", "t", "b", fp="2")])
        self.assertEqual((d["added"], d["dropped"]), ([], []))
        self.assertEqual(keys(d["changed"]), [("TRIGGER", "t", "b")])
        self.assertEqual(d["changed"][0]["table_before"], "a")
        same = diff_objects([obj("TRIGGER", "T", "A", fp="1")], [obj("TRIGGER", "T", "B", fp="1")])
        self.assertEqual(same, no_objects())

    def test_same_name_on_several_tables_needs_same_table(self):
        d = diff_objects([obj("TRIGGER", "t", "a", fp="1"), obj("TRIGGER", "t", "b", fp="1")], [obj("TRIGGER", "t", "a", fp="1"), obj("TRIGGER", "t", "c", fp="1")])
        self.assertEqual(keys(d["added"]), [("TRIGGER", "t", "c")])
        self.assertEqual(keys(d["dropped"]), [("TRIGGER", "t", "b")])

    def test_same_trigger_name_on_two_tables(self):
        d = diff_objects([obj("TRIGGER", "t", "a", fp="1")], [obj("TRIGGER", "t", "a", fp="1"), obj("TRIGGER", "t", "b", fp="1")])
        self.assertEqual(keys(d["added"]), [("TRIGGER", "t", "b")])


class SummaryLinesTest(unittest.TestCase):
    def test_korean_lines(self):
        o = no_objects()
        o["added"] = [{"type": "TRIGGER", "name": "TRG_ORDERS_AUDIT", "table": "ORDERS", "status": "VALID"},
                      {"type": "FUNCTION", "name": "f()", "table": None, "status": None}]
        o["changed"] = [{"type": "PROCEDURE", "name": "P", "table": None, "status": "VALID"}]
        o["invalid"] = [{"type": "PROCEDURE", "name": "P", "table": None, "status": "INVALID"}]
        lines = object_lines(o)
        self.assertEqual(lines[0], "- 트리거 추가 `TRG_ORDERS_AUDIT` (`ORDERS`, 스키마 diff 에 나오지 않음)")
        self.assertEqual(lines[1], "- 함수 추가 `f()` (스키마 diff 에 나오지 않음)")
        self.assertEqual(lines[2], "- 프로시저 정의 변경 `P`")
        self.assertIn("INVALID", lines[3])

    def test_empty(self):
        self.assertEqual(object_lines(None), [])
        self.assertEqual(object_lines(no_objects()), [])


def step_objects(meta, file_prefix):
    return next(r for r in meta["steps"] if r["file"].startswith(file_prefix))["objects"]


@unittest.skipUnless(has_image("postgres:16-alpine"), "postgres:16-alpine 이미지 필요")
class PostgresObjectsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.proc, cls.meta = replay_dir({
            "V1__base.sql": "CREATE TABLE orders (id serial PRIMARY KEY, amt int);\nCREATE TABLE orders_audit (id serial PRIMARY KEY, oid int);\n"
                            "CREATE SEQUENCE ord_no_seq;\n",
            "V2__trigger.sql": "CREATE FUNCTION audit_orders() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                               "INSERT INTO orders_audit(oid) VALUES (NEW.id); RETURN NEW; END $$;\n"
                               "CREATE TRIGGER trg_orders_audit AFTER INSERT ON orders FOR EACH ROW EXECUTE FUNCTION audit_orders();\n",
            "V3__replace.sql": "CREATE OR REPLACE FUNCTION audit_orders() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                               "INSERT INTO orders_audit(oid) VALUES (NEW.id + 0); RETURN NEW; END $$;\n",
            "V4__drop.sql": "DROP TRIGGER trg_orders_audit ON orders;\nDROP FUNCTION audit_orders();\n",
            "V5__again.sql": "CREATE FUNCTION audit_orders() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;\n"
                             "CREATE TRIGGER trg_orders_audit AFTER INSERT ON orders FOR EACH ROW EXECUTE FUNCTION audit_orders();\n",
            "V6__rename.sql": "ALTER TABLE orders RENAME TO orders_v2;\n",
        }, "postgres", cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_function_and_trigger_lifecycle(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        v1, v2, v3, v4 = (step_objects(self.meta, f"V{i}__") for i in (1, 2, 3, 4))
        self.assertEqual(keys(v1["added"]), [("SEQUENCE", "ord_no_seq", None)])
        self.assertEqual(keys(v2["added"]), [("FUNCTION", "audit_orders()", None), ("TRIGGER", "trg_orders_audit", "orders")])
        self.assertEqual(keys(v3["changed"]), [("FUNCTION", "audit_orders()", None)])
        self.assertEqual((v3["added"], v3["dropped"]), ([], []))
        self.assertEqual(keys(v4["dropped"]), [("FUNCTION", "audit_orders()", None), ("TRIGGER", "trg_orders_audit", "orders")])
        self.assertTrue(all(step_objects(self.meta, f"V{i}__")["invalid"] == [] for i in (1, 2, 3, 4)))

    def test_table_rename_keeps_trigger(self):
        v6 = step_objects(self.meta, "V6__")
        self.assertEqual((v6["added"], v6["dropped"]), ([], []))
        self.assertEqual(keys(v6["changed"]), [("TRIGGER", "trg_orders_audit", "orders_v2")])
        self.assertEqual(v6["changed"][0]["table_before"], "orders")

    def test_output_without_objects_shows_neutral_note(self):
        chrome = next((c for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium") if Path(c).exists()), None)
        if not shutil.which("node") or not chrome:
            self.skipTest("node 또는 Chrome 이 없습니다")
        old = self.tmp / "old"
        shutil.copytree(self.tmp / "r", old)
        meta = json.loads((old / "steps.json").read_text())
        for st in meta["steps"]:
            st.pop("objects", None)
        (old / "steps.json").write_text(json.dumps(meta))
        for script, args in (("timeline.py", [str(old), "--lang", "en"]), ("render_timeline.py", [str(old), "--out", str(old / "t.html"), "--lang", "en"])):
            subprocess.run([sys.executable, str(ROOT / "scripts" / script)] + args, check=True, capture_output=True)
        r = subprocess.run(["node", str(ROOT / "tests" / "cdp_eval.mjs"), str(old / "t.html"),
                            "document.getElementById('detail').innerText"], capture_output=True, text=True, timeout=90,
                           env={**__import__("os").environ, "CHROME_PATH": chrome})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("were not checked for this output", json.loads(r.stdout))

    def test_serial_sequences_are_not_listed(self):
        names = [o["name"] for r in self.meta["steps"] for o in r["objects"]["added"]]
        self.assertNotIn("orders_id_seq", names)

    def test_two_ddl_compare_carries_objects_and_keeps_snapshots_clean(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.sql"), Path(d, "b.sql")
            a.write_text("CREATE TABLE t (id int);")
            b.write_text("CREATE TABLE t (id int);\nCREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;\n"
                         "CREATE TRIGGER tg BEFORE INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();")
            scripts = ROOT / "scripts"
            subprocess.run([sys.executable, str(scripts / "snapshot.py"), str(a), str(b), "--out", str(Path(d, "s"))],
                           check=True, capture_output=True)
            s1 = json.loads(Path(d, "s", "01.json").read_text())
            self.assertEqual(keys(s1["objects"]), [("FUNCTION", "f()", None), ("TRIGGER", "tg", "t")])
            subprocess.run([sys.executable, str(scripts / "compare_timeline.py"), str(Path(d, "s", "00.json")), str(Path(d, "s", "01.json")),
                            "--out", str(Path(d, "c")), "--after-sql", str(b)], check=True, capture_output=True)
            meta = json.loads(Path(d, "c", "steps.json").read_text())
            self.assertEqual(keys(meta["steps"][0]["objects"]["added"]), [("FUNCTION", "f()", None), ("TRIGGER", "tg", "t")])
            for i in (0, 1):
                self.assertNotIn("objects", json.loads(Path(d, "c", "snapshots", f"00{i}.json").read_text()))
            subprocess.run([sys.executable, str(scripts / "timeline.py"), str(Path(d, "c"))], check=True, capture_output=True)
            diff = json.loads(Path(d, "c", "diffs", "001.json").read_text())
            self.assertEqual(diff["events"], [])


@unittest.skipUnless(has_image("mysql:8.4"), "mysql:8.4 이미지 필요")
class MySQLObjectsTest(unittest.TestCase):
    def test_delimiter_trigger_in_sample(self):
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "replay.py"), str(ROOT / "samples" / "mysql"), "--db", "mysql",
                                   "--out", d], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            steps = json.loads(Path(d, "steps.json").read_text())["steps"]
            self.assertEqual(keys(steps[4]["objects"]["added"]), [("TRIGGER", "trg_purchase_audit", "purchase")])
            self.assertTrue(all(s["objects"] == no_objects() for s in steps[:4]))


@unittest.skipUnless(has_image("gvenzl/oracle-free:23-slim"), "gvenzl/oracle-free:23-slim 이미지 필요")
class OracleObjectsTest(unittest.TestCase):
    def test_retail_sample_trigger_and_compile_error(self):
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "replay.py"), str(ROOT / "samples" / "oracle_retail"), "--db", "oracle",
                                   "--out", str(Path(d, "r"))], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            steps = json.loads(Path(d, "r", "steps.json").read_text())["steps"]
            v11 = next(s for s in steps if s["file"].startswith("V11__"))["objects"]
            self.assertEqual(keys(v11["added"]), [("TRIGGER", "TRG_ORDERS_AUDIT", "ORDERS")])
            self.assertEqual(v11["added"][0]["status"], "VALID")
            self.assertEqual(v11["invalid"], [])
            for s in steps:
                names = [o["name"] for k in ("added", "dropped") for o in s["objects"][k]]
                self.assertFalse([n for n in names if n.startswith(("ISEQ$$", "SYS_", "BIN$"))], names)

    def test_procedure_with_compile_error_is_invalid_then_fixed(self):
        with tempfile.TemporaryDirectory() as d:
            proc, meta = replay_dir({
                "V1__t.sql": "CREATE TABLE t (id NUMBER);\n",
                "V2__bad.sql": "CREATE OR REPLACE PROCEDURE p_bad AS\nBEGIN\n  missing_proc;\nEND;\n/\n",
                "V3__fix.sql": "CREATE OR REPLACE PROCEDURE p_bad AS\nBEGIN\n  NULL;\nEND;\n/\n",
            }, "oracle", Path(d))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            v1, v2, v3 = (r["objects"] for r in meta["steps"])
            self.assertEqual(v1, no_objects())
            self.assertEqual(keys(v2["added"]), [("PROCEDURE", "P_BAD", None)])
            self.assertEqual(keys(v2["invalid"]), [("PROCEDURE", "P_BAD", None)])
            self.assertEqual(keys(v3["changed"]), [("PROCEDURE", "P_BAD", None)])
            self.assertEqual(v3["invalid"], [])


if __name__ == "__main__":
    unittest.main()
