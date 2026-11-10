"""입력 어댑터(sources.py)와 형식 감지(detect.py) 검증. docker 가 있으면 Supabase·Prisma·Drizzle 샘플을 실제로 재생한다."""
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
from replay import drop_unavailable_extensions  # noqa: E402
from sources import find_drizzle, find_prisma, find_supabase, load  # noqa: E402

SAMPLES = ROOT / "samples"


def has_docker():
    return shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def detect(path):
    return json.loads(subprocess.run([sys.executable, str(ROOT / "scripts" / "detect.py"), str(path)],
                                     check=True, capture_output=True, text=True).stdout)


class AdapterTest(unittest.TestCase):
    def test_supabase_from_repo_root_skips_seed(self):
        steps, _, warnings, root = find_supabase(SAMPLES / "supabase")
        self.assertEqual(root, SAMPLES / "supabase" / "supabase" / "migrations")
        self.assertEqual([s["label"] for s in steps], ["24.03.01", "24.03.12", "24.04.02"])
        self.assertEqual(steps[2]["description"], "message body")
        self.assertEqual(warnings, [])
        self.assertTrue(load("supabase", SAMPLES / "supabase").prelude.exists())

    def test_supabase_bad_name_warns(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d, "supabase", "migrations"); m.mkdir(parents=True)
            (m / "20240101000000_a.sql").write_text("select 1;")
            (m / "notes.sql").write_text("select 1;")
            steps, _, warnings, _ = find_supabase(Path(d))
            self.assertEqual(len(steps), 1)
            self.assertIn("notes.sql", warnings[0])

    def test_prisma_steps_and_provider(self):
        steps, _, warnings, root = find_prisma(SAMPLES / "prisma")
        self.assertEqual(root.name, "migrations")
        self.assertEqual([s["file"] for s in steps], ["20240105120000_init/migration.sql", "20240120093000_add_posts/migration.sql",
                                                     "20240210170000_rename_author/migration.sql"])
        self.assertEqual(warnings, [])
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "migration_lock.toml").write_text('provider = "mysql"\n')
            Path(d, "20240101000000_init").mkdir()
            (Path(d, "20240101000000_init") / "migration.sql").write_text("select 1;")
            with self.assertRaises(SystemExit) as cm:
                find_prisma(Path(d))
            self.assertIn("mysql", str(cm.exception))

    def test_drizzle_follows_journal(self):
        steps, _, warnings, root = find_drizzle(SAMPLES / "drizzle")
        self.assertEqual(root.name, "drizzle")
        self.assertEqual([s["path"].name for s in steps], ["0000_brave_hulk.sql", "0001_tidy_lizard.sql", "0002_silly_storm.sql"])
        self.assertEqual(steps[1]["description"], "tidy lizard")
        self.assertEqual(warnings, [])
        with tempfile.TemporaryDirectory() as d:
            Path(d, "meta").mkdir()
            journal = {"dialect": "postgresql", "entries": [{"idx": 1, "tag": "0001_b", "when": 1717225200000},
                                                            {"idx": 0, "tag": "0000_a", "when": 1714546800000}]}
            (Path(d, "meta") / "_journal.json").write_text(json.dumps(journal))
            for n in ("0000_a", "0001_b", "0002_orphan"):
                (Path(d) / f"{n}.sql").write_text("select 1;")
            steps, _, warnings, _ = find_drizzle(Path(d))
            self.assertEqual([s["path"].name for s in steps], ["0000_a.sql", "0001_b.sql"])
            self.assertIn("0002_orphan.sql", warnings[0])
            (Path(d) / "0001_b.sql").unlink()
            with self.assertRaises(SystemExit):
                find_drizzle(Path(d))
            journal["dialect"] = "mysql"
            (Path(d, "meta") / "_journal.json").write_text(json.dumps(journal))
            with self.assertRaises(SystemExit):
                find_drizzle(Path(d))

    def test_unavailable_extensions_are_commented_out(self):
        sql = ('CREATE EXTENSION IF NOT EXISTS "pg_graphql" WITH SCHEMA "graphql";\n'
               "COMMENT ON EXTENSION pg_graphql IS 'x';\n"
               'create extension if not exists "pgcrypto" with schema extensions;\n'
               "select 'CREATE EXTENSION pg_net;';\n")
        out, gone = drop_unavailable_extensions(sql, {"pgcrypto", "uuid-ossp"})
        self.assertEqual(gone, ["pg_graphql"])
        self.assertNotIn('\nCOMMENT ON EXTENSION pg_graphql', "\n" + out)
        self.assertIn('create extension if not exists "pgcrypto"', out)
        self.assertIn("select 'CREATE EXTENSION pg_net;';", out)

    def test_prisma_baseline_zero_init_comes_first(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d, "prisma", "migrations")
            for n in ("20240101000000_a", "0_init"):
                (m / n).mkdir(parents=True)
                (m / n / "migration.sql").write_text("select 1;")
            (m / "migration_lock.toml").write_text('provider = "postgresql"\n')
            steps, _, warnings, _ = find_prisma(Path(d))
            self.assertEqual([s["file"] for s in steps], ["0_init/migration.sql", "20240101000000_a/migration.sql"])
            self.assertEqual(warnings, [])

    def test_drizzle_broken_journal_and_folder_layout(self):
        with tempfile.TemporaryDirectory() as d:
            j = Path(d, "drizzle", "meta"); j.mkdir(parents=True)
            (j / "_journal.json").write_text("{bad")
            with self.assertRaises(SystemExit):
                find_drizzle(Path(d))
            (j / "_journal.json").write_text(json.dumps({"entries": [{"idx": 0}]}))
            with self.assertRaises(SystemExit):
                find_drizzle(Path(d))
        with tempfile.TemporaryDirectory() as d:
            for n in ("20250201000000_b", "20250101000000_a"):
                Path(d, "drizzle", n).mkdir(parents=True)
                Path(d, "drizzle", n, "migration.sql").write_text("select 1;")
            steps, _, warnings, _ = find_drizzle(Path(d))
            self.assertEqual([s["file"] for s in steps], ["20250101000000_a/migration.sql", "20250201000000_b/migration.sql"])
            self.assertIn("journal", warnings[0])

    def test_extension_skip_respects_statements_and_quotes(self):
        av = {"pgcrypto"}
        out, gone = drop_unavailable_extensions("CREATE EXTENSION pg_net; CREATE EXTENSION pgcrypto;\n", av)
        self.assertEqual(gone, ["pg_net"])
        self.assertIn("\n CREATE EXTENSION pgcrypto;", out)
        body = "DO $$ BEGIN\n    CREATE EXTENSION IF NOT EXISTS pg_net; END $$;"
        self.assertEqual(drop_unavailable_extensions(body, av), (body, []))
        out, gone = drop_unavailable_extensions("COMMENT ON EXTENSION pg_graphql IS 'a;b'", av)
        self.assertEqual((out.startswith("-- [schema-timeline]"), gone), (True, ["pg_graphql"]))

    def test_supabase_platform_roles_are_not_reported_as_created(self):
        self.assertIn("authenticated", load("supabase", SAMPLES / "supabase").platform_roles)

    def test_detect_reports_tool_formats(self):
        for name in ("supabase", "prisma", "drizzle"):
            d = detect(SAMPLES / name)
            self.assertEqual((d["format"], d["db_type"], d["steps"], d["supported"]), (name, "postgresql", 3, True), d)
        self.assertEqual(detect(SAMPLES / "flyway")["format"], "flyway")


@unittest.skipUnless(has_docker(), "docker 필요")
class ToolReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.out = {}
        for name in ("supabase", "prisma", "drizzle"):
            out = Path(cls.tmp) / name
            subprocess.run([sys.executable, str(ROOT / "scripts" / "replay.py"), str(SAMPLES / name), "--format", name, "--out", str(out)],
                           check=True, capture_output=True)
            subprocess.run([sys.executable, str(ROOT / "scripts" / "timeline.py"), str(out)], check=True, capture_output=True)
            cls.out[name] = out

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def load(self, name, f):
        return json.loads((self.out[name] / f).read_text())

    def renamed(self, name):
        return [(e["table"], e["before"], e["after"], e["confidence"]) for e in self.load(name, "events.json") if e["kind"] == "column_renamed"]

    def test_all_applied(self):
        for name in self.out:
            self.assertTrue(all(r["status"] == "applied" for r in self.load(name, "steps.json")["steps"]), name)

    def test_supabase_prelude_auth_fk_and_skipped_extension(self):
        meta = self.load("supabase", "steps.json")
        self.assertEqual(meta["prelude"], "supabase.sql")
        self.assertEqual(meta["roles_created"], [])
        self.assertIn("pg_graphql", " ".join(meta["steps"][0]["warnings"]))
        snap = self.load("supabase", "snapshots/003.json")
        self.assertEqual(sorted(t["name"] for t in snap["tables"]), ["channels", "messages", "profiles"])
        profiles = next(t for t in snap["tables"] if t["name"] == "profiles")
        self.assertIn("REFERENCES auth.users(id) ON DELETE CASCADE", profiles["ddl"])
        self.assertEqual(self.renamed("supabase"), [("messages", "content", "body", "confirmed")])

    def test_prisma_enum_and_manual_rename(self):
        snap = self.load("prisma", "snapshots/003.json")
        self.assertEqual(snap["types"], [{"name": "Role", "kind": "enum", "labels": ["USER", "ADMIN"]}])
        self.assertEqual(snap["schema"][0]["kind"], "type")
        self.assertEqual(self.renamed("prisma"), [("Post", "authorId", "writerId", "confirmed")])
        self.assertTrue(any(e["kind"] == "column_dropped" and e["column"] == "name" for e in self.load("prisma", "events.json")))

    def test_drizzle_breakpoints_and_rename(self):
        self.assertEqual(self.renamed("drizzle"), [("users", "full_name", "name", "confirmed")])
        orders = next(t for t in self.load("drizzle", "snapshots/003.json")["tables"] if t["name"] == "orders")
        self.assertIn("NUMERIC(14,2)", orders["ddl"])


if __name__ == "__main__":
    unittest.main()
