"""입력 어댑터(sources.py)와 형식 감지(detect.py) 검증. docker 가 있으면 Supabase·Prisma·Drizzle 샘플을 실제로 재생한다."""
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
from replay import (drop_unavailable_extensions, failure_hint, make_substitute, parse_pairs, secret_placeholders,  # noqa: E402
                    unresolved_placeholders, unused_substitutions)
import engines  # noqa: E402
from sources import find_drizzle, find_prisma, find_sql_files, find_supabase, guess_format, load  # noqa: E402

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


def touch(root, *names, body="select 1;"):
    """root 아래에 names 파일들을 만든다. 디렉터리는 함께 만든다."""
    for n in names:
        f = Path(root, n)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)


class RealWorldLayoutTest(unittest.TestCase):
    """이름 없는 Prisma 마이그레이션, up/down 짝, 두 자리 번호, 모노레포, --format 생략 배치."""

    def test_prisma_unnamed_migration_is_a_step(self):
        with tempfile.TemporaryDirectory() as d:
            touch(d, "20220721183745_/migration.sql", "20220801000000_add_x/migration.sql")
            touch(d, "migration_lock.toml", body='provider = "postgresql"\n')
            steps, _, warnings, _ = find_prisma(Path(d))
            self.assertEqual([s["file"] for s in steps], ["20220721183745_/migration.sql", "20220801000000_add_x/migration.sql"])
            self.assertEqual((steps[0]["description"], warnings), ("20220721183745_", []))

    def test_up_down_pairs_and_two_digit_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            touch(d, "00_init_schema.up.sql", "000002_add_users.up.sql", "000002_add_users.down.sql", "20210710035447_alter.UP.sql")
            steps, _, warnings = find_sql_files(Path(d))
            self.assertEqual([s["path"].name for s in steps], ["00_init_schema.up.sql", "000002_add_users.up.sql", "20210710035447_alter.UP.sql"])
            self.assertEqual([s["description"] for s in steps], ["init schema", "add users", "alter"])
            self.assertEqual(len(warnings), 1)
            self.assertIn("000002_add_users.down.sql", warnings[0])

    def test_monorepo_histories_found_below_root(self):
        with tempfile.TemporaryDirectory() as d:
            touch(d, "packages/db/prisma/migrations/20240101000000_init/migration.sql")
            touch(d, "packages/db/prisma/migrations/migration_lock.toml", body='provider = "postgresql"\n')
            touch(d, "node_modules/x/prisma/migrations/migration_lock.toml", body='provider = "postgresql"\n')
            self.assertEqual(find_prisma(Path(d))[3], Path(d, "packages/db/prisma/migrations"))
        with tempfile.TemporaryDirectory() as d:
            touch(d, "apps/web/drizzle/0000_a.sql")
            touch(d, "apps/web/drizzle/meta/_journal.json", body=json.dumps({"dialect": "postgresql", "entries": [{"idx": 0, "tag": "0000_a"}]}))
            self.assertEqual(find_drizzle(Path(d))[3], Path(d, "apps/web/drizzle"))
        with tempfile.TemporaryDirectory() as d:
            touch(d, "apps/api/supabase/migrations/20240101000000_a.sql")
            self.assertEqual(find_supabase(Path(d))[3], Path(d, "apps/api/supabase/migrations"))

    def test_monorepo_with_two_histories_asks_for_a_path(self):
        with tempfile.TemporaryDirectory() as d:
            for app in ("a", "b"):
                touch(d, f"apps/{app}/prisma/migrations/20240101000000_init/migration.sql")
                touch(d, f"apps/{app}/prisma/migrations/migration_lock.toml", body='provider = "postgresql"\n')
            with self.assertRaises(SystemExit) as cm:
                find_prisma(Path(d))
            self.assertIn("apps/a/prisma/migrations", str(cm.exception))
            self.assertIn("apps/b/prisma/migrations", str(cm.exception))

    def test_guess_format(self):
        self.assertEqual(guess_format(SAMPLES / "flyway"), ("flyway", SAMPLES / "flyway"))
        for name in ("supabase", "prisma", "drizzle"):
            self.assertEqual(guess_format(SAMPLES / name)[0], name)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(guess_format(Path(d)), (None, Path(d)))
            touch(d, "db/000001_a.up.sql", "db/000001_a.down.sql")
            self.assertIsNone(guess_format(Path(d))[0])
            touch(d, "db/000002_b.up.sql")
            self.assertEqual(guess_format(Path(d)), ("sqlfiles", Path(d, "db").resolve()))
            touch(d, "prisma/migrations/migration_lock.toml", body='provider = "postgresql"\n')
            self.assertEqual(guess_format(Path(d))[0], "prisma")
            touch(d, "drizzle/meta/_journal.json", body="{}")
            with self.assertRaises(SystemExit):
                guess_format(Path(d))

    def test_guess_format_ignores_vendored_and_seed_sql(self):
        with tempfile.TemporaryDirectory() as d:
            touch(d, "db/migrations/000001_a.up.sql", "db/migrations/000002_b.up.sql",
                  "node_modules/pkg/sql/001_init.sql", "node_modules/pkg/sql/002_x.sql",
                  "build/out/000003_gen.sql", "build/out/000004_gen.sql", "seeds/01_users.sql", "seeds/02_orders.sql")
            self.assertEqual(guess_format(Path(d)), ("sqlfiles", Path(d, "db/migrations").resolve()))
            touch(d, "seeds/dev/01_a.sql", "seeds/dev/02_b.sql", "tests/sql/01_x.sql", "tests/sql/02_y.sql")
            self.assertEqual(guess_format(Path(d))[1], Path(d, "db/migrations").resolve())
            self.assertEqual(guess_format(Path(d, "seeds"))[1], Path(d, "seeds").resolve())
            touch(d, "db/migrations/v2/003_c.sql", "db/migrations/v2/004_d.sql")
            self.assertEqual(guess_format(Path(d))[1], Path(d, "db/migrations").resolve())
            touch(d, "other/001_a.sql", "other/002_b.sql")
            with self.assertRaises(SystemExit) as cm:
                guess_format(Path(d))
            self.assertIn("db/migrations", str(cm.exception))

    def test_guess_format_mixed_flyway_and_prisma_stops(self):
        with tempfile.TemporaryDirectory() as d:
            touch(d, "api/db/migration/V1__init.sql")
            touch(d, "web/prisma/migrations/migration_lock.toml", body='provider = "postgresql"\n')
            with self.assertRaises(SystemExit) as cm:
                guess_format(Path(d))
            self.assertIn("flyway", str(cm.exception))

    def test_guess_format_prisma_without_lock_and_relative_supabase(self):
        with tempfile.TemporaryDirectory() as d:
            touch(d, "prisma/migrations/20240101000000_init/migration.sql")
            self.assertEqual(guess_format(Path(d))[0], "prisma")
            self.assertEqual(len(find_prisma(Path(d))[0]), 1)
        with tempfile.TemporaryDirectory() as d:
            touch(d, "supabase/migrations/20240101000000_a.sql", "supabase/migrations/20240102000000_b.sql")
            cwd = os.getcwd()
            try:
                os.chdir(Path(d, "supabase", "migrations"))
                self.assertEqual(guess_format(Path("."))[0], "supabase")
            finally:
                os.chdir(cwd)

    def test_replay_with_file_and_no_format_uses_git_snapshots(self):
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "replay.py"), d, "--out", str(Path(d, "out")), "--file", "schema.sql"],
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("git", proc.stderr.lower())
            self.assertNotIn("No migrations", proc.stderr)


class SubstituteAndHintTest(unittest.TestCase):
    """--placeholder·--replace 치환, 실패 힌트, 감지 결과의 자리표시자 안내."""

    def test_placeholders_and_flyway_defaults(self):
        sub = make_substitute({"schemaName": "app"}, [], True, "public")
        self.assertEqual(sub("ALTER TABLE ${schemaName}.a; -- ${flyway:defaultSchema} ${flyway:filename} ${other}", "V1__a.sql"),
                         "ALTER TABLE app.a; -- public V1__a.sql ${other}")
        self.assertEqual(make_substitute({}, [], False, "public")("${flyway:defaultSchema}", "x.sql"), "${flyway:defaultSchema}")
        self.assertEqual(make_substitute({"flyway:defaultSchema": "s"}, [], True, "public")("${flyway:defaultSchema}", "x"), "s")

    def test_replace_ignores_whitespace_differences(self):
        sub = make_substitute({}, parse_pairs(['{{ index .Options "Namespace" }}=auth'], "--replace"), False, "public")
        self.assertEqual(sub('{{ index .Options "Namespace" }}.users, {{index .Options "Namespace" }}.x, {{  index .Options  "Namespace"}}.y', "a"),
                         "auth.users, auth.x, auth.y")
        self.assertEqual(make_substitute({}, [("a=b", "c")], False, "p")("a=b", "x"), "c")
        tight = make_substitute({}, parse_pairs(['{{index .Options "Namespace"}}=auth', "{{.Ns}}=n"], "--replace", last=True), False, "p")
        self.assertEqual(tight('{{ index .Options "Namespace" }}.a {{ .Ns }}.b {{indexx .Options "Namespace"}}', "f"),
                         'auth.a n.b {{indexx .Options "Namespace"}}')

    def test_placeholders_replace_once_and_split_rules(self):
        self.assertEqual(make_substitute({"a": "${b}", "b": "X"}, [], False, "p")("${a} ${b}", "f"), "${b} X")
        self.assertEqual(parse_pairs(["{{ x := 1 }}=v"], "--replace", last=True), [("{{ x := 1 }}", "v")])
        self.assertEqual(parse_pairs(["k=v=w"], "--placeholder"), [("k", "v=w")])

    def test_unused_substitutions_and_secret_values(self):
        self.assertEqual(unused_substitutions(["select ${a}", "{{ .Ns }}.t"], {"a": "1", "b": "2"}, [("{{.Ns}}", "x"), ("{{.Other}}", "y")]),
                         ["--placeholder b", "--replace {{.Other}}"])
        values = {"dbPassword": "s3cret!", "jwt_secret": "abcd", "apiKey": "k-123", "idKeyType": "bigint", "keyspace": "ks01",
                  "passengerSchema": "pass", "token": "abc", "schema": "app"}
        self.assertEqual(secret_placeholders(values), {"dbPassword": "s3cret!", "jwt_secret": "abcd", "apiKey": "k-123"})
        self.assertEqual(sorted(secret_placeholders({"private_key": "pkey", "AccessKey": "akey", "primaryKey": "pk_x"})), ["AccessKey", "private_key"])
        shown = make_substitute({**values, "dbPassword": "********"}, [], False, "p")
        self.assertEqual(shown("CREATE USER ${schema} IDENTIFIED BY ${dbPassword}; CREATE TABLE t (id ${idKeyType}, s3cret! int)", "f"),
                         "CREATE USER app IDENTIFIED BY ********; CREATE TABLE t (id bigint, s3cret! int)")

    def test_parse_pairs_rejects_missing_value_sign(self):
        self.assertEqual(parse_pairs(["k=v=w"], "--placeholder"), [("k", "v=w")])
        for bad in (["novalue"], ["=v"]):
            with self.assertRaises(SystemExit):
                parse_pairs(bad, "--placeholder")

    def test_unresolved_placeholders_lists_files(self):
        steps = [{"text": "select ${a}, ${b};", "file": "V1__x.sql"}, {"text": "select ${a};", "file": "V2__y.sql"}]
        self.assertEqual(unresolved_placeholders(steps, lambda s: s["text"]), {"a": ["V1__x.sql", "V2__y.sql"], "b": ["V1__x.sql"]})

    def test_failure_hints(self):
        pg, my = engines.get("postgres"), engines.get("mysql")
        self.assertIn("pgvector/pgvector:pg16", failure_hint('ERROR:  type "extensions.vector" does not exist', "", pg))
        self.assertIn("postgis/postgis", failure_hint('ERROR:  extension "postgis" is not available', "", pg))
        self.assertIn("--image", failure_hint('could not open extension control file "/usr/share/postgresql/extension/pg_foo.control"', "", pg))
        self.assertIn("--replace", failure_hint('ERROR:  syntax error at or near "{"', "ALTER TABLE {{ .Ns }}.a", pg))
        self.assertIn("--placeholder", failure_hint("ERROR 1064 (42000): You have an error in your SQL syntax", "ALTER TABLE ${s}.a", my))
        self.assertIsNone(failure_hint('ERROR:  type "vector" does not exist', "", my))
        self.assertIsNone(failure_hint('ERROR:  syntax error at or near "x"', "select x", pg))
        self.assertIn("postgis/postgis:14-3.5", failure_hint('ERROR:  type "geometry" does not exist', "", pg, (), "postgres:14-alpine"))
        for image, want in (("timescale/timescaledb:latest-pg15", "pg15"), ("localhost:5000/postgres:14", "pg14"), ("postgres:16.4", "pg16"),
                            ("postgres", "pg16"), ("postgres@sha256:12ab34", "pg16")):
            self.assertIn(f"pgvector/pgvector:{want}", failure_hint('ERROR:  type "vector" does not exist', "", pg, (), image))
        self.assertIn("CREATE EXTENSION vector", failure_hint('ERROR:  type "vector" does not exist', "", pg, {"vector"}, "pgvector/pgvector:pg16"))
        self.assertIsNone(failure_hint("ERROR: x", "plv8 body `${\n x }`", pg))
        self.assertEqual(unresolved_placeholders([{"text": "`${\n x }` ${ok}", "file": "V1__a.sql"}], lambda s: s["text"]), {"ok": ["V1__a.sql"]})

    def test_detect_reports_templates_and_placeholders(self):
        with tempfile.TemporaryDirectory() as d:
            touch(d, "migrations/00_init.up.sql", body='CREATE TABLE {{ index .Options "Namespace" }}.users (id int);')
            touch(d, "migrations/01_b.up.sql", body='ALTER TABLE {{index .Options "Namespace" }}.users ADD x int;')
            out = detect(Path(d))
            self.assertEqual(out["templates_used"], ['{{ index .Options "Namespace" }}'])
            self.assertTrue(any("--replace" in w for w in out["warnings"]))
        with tempfile.TemporaryDirectory() as d:
            touch(d, "db/V1__a.sql", body="CREATE TABLE ${schemaName}.a (id int);")
            touch(d, "db/V2__b.sql", body="SET search_path = ${flyway:defaultSchema}; CREATE TABLE {{.Ns}}.b (id int);")
            out = detect(Path(d))
            self.assertEqual(out["placeholders_used"], ["schemaName"])
            self.assertEqual(out["templates_used"], ["{{.Ns}}"])
            self.assertTrue(any("--placeholder" in w for w in out["warnings"]))

    def test_detect_counts_baseline_steps(self):
        with tempfile.TemporaryDirectory() as d:
            touch(d, "db/V1__a.sql", "db/V2__b.sql", "db/B2__base.sql", "db/V3__c.sql")
            out = detect(Path(d))
            self.assertEqual((out["steps"], out["migration_dirs"][0]["baseline"]), (2, "B2__base.sql"))
            self.assertTrue(any("B2__base.sql" in w and "2" in w for w in out["warnings"]))
        with tempfile.TemporaryDirectory() as d:
            touch(d, "db/V1__a.sql", "db/B2__base.sql", "db/sub/V3__c.sql", "db/sub/V4__d.sql")
            out = detect(Path(d))
            self.assertEqual(out["steps"], 3)
            self.assertFalse(any("-1" in w for w in out["warnings"]))
            self.assertEqual([(x["steps"], x["baseline_skipped"]) for x in out["migration_dirs"]], [(1, 1), (2, 0)])
        with tempfile.TemporaryDirectory() as d:
            touch(d, "db/V1__a.sql", "db/V2__b.sql", "db/sub/B2__base.sql")
            self.assertEqual([x["steps"] for x in detect(Path(d))["migration_dirs"]], [0, 1])
        with tempfile.TemporaryDirectory() as d:
            touch(d, "db/B1__base.sql")
            self.assertEqual((detect(Path(d))["format"], guess_format(Path(d))[0]), ("flyway", "flyway"))


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
