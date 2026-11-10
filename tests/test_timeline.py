"""마이그레이션 이력 재생(replay.py)·단계 diff·계보(timeline.py) 검증.

docker 가 없으면 재생이 필요한 테스트는 건너뛴다.
"""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from diff import diff  # noqa: E402
from replay import analyze, find_steps, referenced_roles  # noqa: E402
from sqltext import mask_secrets, mask_snapshot, scrub  # noqa: E402
from timeline import attach_ids, event_id  # noqa: E402

SAMPLE = ROOT / "samples" / "flyway"


def has_docker():
    return shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


class StepOrderTest(unittest.TestCase):
    def test_versions_sort_numerically_with_dot_and_underscore(self):
        steps, skipped, _ = find_steps(SAMPLE)
        self.assertEqual([s["version"] for s in steps],
                         ["1", "1.1", "2", "3", "4", "5", "6", "7", "8", "8.1", "10"])
        self.assertEqual(skipped, [])

    def test_trailing_zero_versions_collide(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("V1__a.sql", "V1.0__b.sql"):
                (Path(d) / name).write_text("SELECT 1;")
            with self.assertRaises(SystemExit):
                find_steps(Path(d))

    def test_repeatable_and_callbacks_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("V1__a.sql", "R__view.sql", "afterMigrate.sql"):
                (Path(d) / name).write_text("SELECT 1;")
            steps, skipped, _ = find_steps(Path(d))
            self.assertEqual([s["path"].name for s in steps], ["V1__a.sql"])
            self.assertEqual(sorted(f.name for f in skipped), ["R__view.sql", "afterMigrate.sql"])

    def test_password_is_masked(self):
        sql = ("CREATE USER a WITH PASSWORD 'x''y';\n"
               "CREATE USER b PASSWORD E'se\\'cret';\n"
               "CREATE USER c ENCRYPTED PASSWORD $$dollar$$;\n"
               "SELECT dblink_connect('host=h password=conn1 user=u');\n"
               "SELECT 'password is not here';")
        masked, secrets = mask_secrets(sql)
        for leak in ("x'y", "se\\'cret", "dollar", "conn1"):
            self.assertNotIn(leak, masked)
        self.assertIn("'password is not here'", masked)
        err = "ERROR: bad\nLINE 1: ...PASSWORD 'dollar' VALID\n        ^\nDETAIL: dollar"
        self.assertEqual(scrub(err, secrets), "ERROR: bad\nDETAIL: ********")

    def test_secrets_inside_strings_and_dollar_bodies(self):
        masked, secrets = mask_secrets("DO $$ BEGIN CREATE ROLE x PASSWORD 'dosecret'; END $$;\n"
                                       "COMMENT ON COLUMN a.note IS 'pw: PASSWORD ''colcmt''';")
        self.assertNotIn("dosecret", masked)
        self.assertNotIn("colcmt", masked)
        self.assertIn("dosecret", secrets)

    def test_known_secret_does_not_touch_sql_outside_literals(self):
        doc = {"tables": [{"comment": "pw: PASSWORD 'colcmt' text", "columns": [{"comment": None, "default": "'a'::text"}],
                           "constraints": [], "indexes": []}],
               "views": [{"definition": " SELECT 'text'::text AS t;"}]}
        mask_snapshot(doc, ["text"])
        self.assertEqual(doc["tables"][0]["columns"][0]["default"], "'a'::text")
        self.assertEqual(doc["tables"][0]["comment"], "pw: PASSWORD '********' text")
        self.assertEqual(doc["views"][0]["definition"], " SELECT '********'::text AS t;")

    def test_roles_referenced_but_not_created(self):
        sql = ["CREATE USER app; GRANT SELECT ON t TO app, report; ALTER TABLE t OWNER TO owner_role;",
               "-- GRANT ALL ON t TO commented;",
               'GRANT SELECT ON t TO "ReportUser", pg_monitor; CREATE FUNCTION f() RETURNS void AS $$ GRANT x TO inbody $$ LANGUAGE sql;']
        self.assertEqual(referenced_roles(sql), ["ReportUser", "owner_role", "report"])

    def test_dml_detection(self):
        self.assertTrue(analyze("UPDATE ONLY t SET a = 1;")[0])
        self.assertTrue(analyze("COPY t (a, b) FROM '/x';")[0])
        self.assertFalse(analyze("GRANT TRUNCATE ON t TO PUBLIC;")[0])
        self.assertFalse(analyze("WITH x AS (SELECT 1 FROM a FOR UPDATE) SELECT 1;")[0])
        self.assertTrue(analyze("WITH x AS (DELETE FROM a RETURNING *) SELECT 1;")[0])
        self.assertFalse(analyze("DO $$ BEGIN INSERT INTO t VALUES (1); END $$;")[0])
        self.assertFalse(analyze("SELECT 'a--b'; INSERT_X;")[0])
        self.assertFalse(analyze("CREATE INDEX CONCURRENTLY i ON t (a);")[1])
        self.assertFalse(analyze("BEGIN; CREATE TABLE x (); COMMIT;")[1])
        self.assertTrue(analyze("CREATE TABLE x (a int);")[1])


def snap(tables, rid="r"):
    """합성 스냅샷. tables 는 (oid, 이름, [(attnum, 컬럼, 타입)], 제약 목록)."""
    out = []
    for oid, name, cols, cons in tables:
        out.append({"name": name, "oid": oid, "comment": None, "indexes": [], "constraints": cons,
                    "columns": [{"name": c, "ordinal": n, "type": ty, "nullable": True, "default": None,
                                 "comment": None, "identity": None} for n, c, ty in cols]})
    return {"tables": out, "views": [], "replay_id": rid}


def fk(name, col, ref, ref_col):
    return {"name": name, "kind": "f", "columns": [col], "ref_table": ref, "ref_columns": [ref_col],
            "definition": f"FOREIGN KEY ({col}) REFERENCES {ref}({ref_col})"}


class OidDiffTest(unittest.TestCase):
    def ids(self, b, a):
        d = diff(b, a)
        attach_ids(d, b, a)
        return d, {(e["kind"], e.get("before"), e.get("after")): event_id(e) for e in d["events"]}

    def test_drop_column_then_rename_another_into_its_name(self):
        b = snap([(1, "m", [(1, "id", "int"), (2, "code", "text"), (3, "x", "text")], [])])
        a = snap([(1, "m", [(1, "id", "int"), (3, "code", "text")], [])])
        d, ids = self.ids(b, a)
        self.assertEqual(ids[("column_dropped", "text", None)], "c1.2")
        self.assertEqual(ids[("column_renamed", "x", "code")], "c1.3")
        self.assertEqual({c["name"]: c["id"] for c in d["tables"][0]["columns"] if c["status"] == "dropped"}, {"code": "c1.2"})

    def test_swap_table_keeps_ids_apart(self):
        b = snap([(1, "orders", [(1, "id", "int")], []), (2, "orders_new", [(1, "id", "int")], [])])
        a = snap([(2, "orders", [(1, "id", "int")], [])])
        _, ids = self.ids(b, a)
        self.assertEqual(ids[("table_dropped", "orders", None)], "t1")
        self.assertEqual(ids[("table_renamed", "orders_new", "orders")], "t2")

    def test_referenced_column_rename_does_not_churn_fk(self):
        b = snap([(1, "members", [(1, "id", "int")], []), (2, "child", [(1, "m", "int")], [fk("child_m_fkey", "m", "members", "id")])])
        a = snap([(1, "members", [(1, "member_id", "int")], []),
                  (2, "child", [(1, "m", "int")], [dict(fk("child_m_fkey", "m", "members", "member_id"))])])
        d, _ = self.ids(b, a)
        self.assertEqual([e["kind"] for e in d["events"]], ["column_renamed"])


@unittest.skipUnless(has_docker(), "docker 필요")
class ReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        out = Path(cls.tmp)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "replay.py"), str(SAMPLE), "--out", str(out)],
                       check=True, capture_output=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "timeline.py"), str(out)], check=True, capture_output=True)
        cls.meta = json.loads((out / "steps.json").read_text())
        cls.events = json.loads((out / "events.json").read_text())
        cls.lineage = json.loads((out / "lineage.json").read_text())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def ev(self, step, kind, **kw):
        return [e for e in self.events if e["step"] == step and e["kind"] == kind
                and all(e.get(k) == v for k, v in kw.items())]

    def by_name(self, kind, *names):
        return next(e for e in self.lineage.values() if e["kind"] == kind and [n["name"] for n in e["names"]] == list(names))

    def test_all_steps_applied_and_missing_role_created(self):
        self.assertTrue(all(s["status"] == "applied" for s in self.meta["steps"]))
        self.assertEqual(self.meta["roles_created"], ["report_role"])
        self.assertIn("'********'", self.meta["steps"][6]["sql"])
        self.assertEqual([s["dml"] for s in self.meta["steps"]].count(True), 2)

    def test_renames_are_confirmed_by_oid(self):
        self.assertTrue(self.ev(4, "column_renamed", before="state", after="status", confidence="confirmed"))
        self.assertTrue(self.ev(6, "table_renamed", before="order_items", after="order_lines", confidence="confirmed"))
        self.assertTrue(self.ev(8, "column_renamed", before="phone", after="mobile", confidence="confirmed"))

    def test_rename_with_type_change_in_same_step(self):
        self.assertTrue(self.ev(5, "column_renamed", before="name", after="title", confidence="confirmed"))
        self.assertTrue(self.ev(5, "column_type", table="products", column="title"))
        self.assertFalse(self.ev(5, "column_added") + self.ev(5, "column_dropped"))

    def test_drop_and_recreate(self):
        self.assertTrue(self.ev(6, "column_dropped", table="orders", column="memo"))
        self.assertTrue(self.ev(8, "table_recreated", table="codes"))
        codes = [e for e in self.lineage.values() if e["kind"] == "table" and e["names"][0]["name"] == "codes"]
        self.assertEqual(sorted((e["created"], e["dropped"]) for e in codes), [(2, 8), (8, None)])

    def test_rerun_into_same_dir_drops_stale_outputs(self):
        with tempfile.TemporaryDirectory() as m:
            shutil.copy(SAMPLE / "V1__init.sql", m)
            Path(m, "V2__broken.sql").write_text("ALTER TABLE nope ADD COLUMN x int;")
            Path(m, "V3__never.sql").write_text("SELECT 1;")
            out = Path(self.tmp)
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "replay.py"), m, "--out", str(out)], capture_output=True)
            self.assertEqual(r.returncode, 1)
            subprocess.run([sys.executable, str(ROOT / "scripts" / "timeline.py"), str(out)], check=True, capture_output=True)
            meta = json.loads((out / "steps.json").read_text())
            self.assertEqual([s["status"] for s in meta["steps"]], ["applied", "failed", "not_run"])
            self.assertEqual(sorted(p.name for p in (out / "diffs").iterdir()), ["001.json"])
            self.assertEqual(sorted(p.name for p in (out / "snapshots").iterdir()), ["000.json", "001.json"])

    def test_lineage_follows_renames(self):
        phone = self.by_name("column", "phone", "mobile")
        self.assertEqual(phone["created"], 3)
        self.assertEqual([(e["step"], e["kind"]) for e in phone["events"]],
                         [(3, "column_added"), (8, "column_renamed"), (10, "column_nullable")])
        lines = self.by_name("table", "order_items", "order_lines")
        self.assertIsNone(lines["dropped"])


if __name__ == "__main__":
    unittest.main()


class FormatTest(unittest.TestCase):
    def test_numbered_sql_files_init_first_then_number_order(self):
        from replay import find_sql_files
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "init").mkdir()
            for name in ("init/init.sql", "20250723095000_add_x.sql", "20250701121000__alter_y.sql",
                         "202601010000__z.sql", "status.md", "notes.sql"):
                (Path(d) / name).write_text("SELECT 1;")
            steps, _, warnings = find_sql_files(Path(d))
            self.assertEqual([s["path"].name for s in steps],
                             ["init.sql", "20250701121000__alter_y.sql", "20250723095000_add_x.sql", "202601010000__z.sql"])
            self.assertEqual([s["label"] for s in steps], ["init", "25.07.01", "25.07.23", "26.01.01"])
            self.assertTrue(any("notes.sql" in w for w in warnings))

    def test_git_snapshots_one_step_per_revision(self):
        from replay import find_git_snapshots
        with tempfile.TemporaryDirectory() as d:
            git = lambda *a: subprocess.run(["git", "-C", d, *a], check=True, capture_output=True)
            git("init", "-q")
            git("config", "user.email", "t@example.com"); git("config", "user.name", "t")
            for k, body in enumerate(["CREATE TABLE a (id int);", "CREATE TABLE a (id int, b text);"]):
                Path(d, "schema.sql").write_text(body)
                git("add", "schema.sql"); git("commit", "-qm", f"rev {k}")
            steps, _, _ = find_git_snapshots(Path(d), "schema.sql")
            self.assertEqual([s["description"] for s in steps], ["rev 0", "rev 1"])
            self.assertIn("b text", steps[1]["text"])
            self.assertTrue(all(s["full"] for s in steps))

    def test_link_by_name_keeps_ids_across_full_snapshots(self):
        from timeline import link_by_name
        s0 = {"tables": [], "views": []}
        s1 = snap([(500, "a", [(1, "id", "int"), (2, "x", "text")], [])], rid="r1")
        s2 = snap([(900, "a", [(1, "id", "int"), (2, "x", "text"), (3, "y", "int")], [])], rid="r2")
        link_by_name([s0, s1, s2])
        self.assertEqual(s1["tables"][0]["oid"], s2["tables"][0]["oid"])
        self.assertEqual([c["ordinal"] for c in s2["tables"][0]["columns"]], [1, 2, 3])
