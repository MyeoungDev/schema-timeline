"""DB 엔진(engines.py, engine_mysql.py, engine_oracle.py)과 이름 변경 id 잇기(idmap.py) 검증.

idmap·감지 테스트는 docker 없이 돈다. 엔진별 재생 테스트는 그 엔진 이미지가 로컬에 있을 때만 돈다(받지 않는다).
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
from idmap import IdMap, hints  # noqa: E402
from replay import analyze  # noqa: E402
from scriptsplit import normalize, oracle_script  # noqa: E402
from sources import find_prisma  # noqa: E402
from sqltext import mask_secrets  # noqa: E402


def has_image(image):
    if not shutil.which("docker") or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return False
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0


def doc(*tables):
    return {"tables": [{"name": n, "columns": [{"name": c} for c in cols]} for n, cols in tables]}


def ids(d):
    return {t["name"]: (t["oid"], {c["name"]: c["ordinal"] for c in t["columns"]}) for t in d["tables"]}


class HintTest(unittest.TestCase):
    def test_mysql_statements(self):
        h = hints("RENAME TABLE `a` TO b, c TO `d`;\n"
                  "ALTER TABLE orders CHANGE COLUMN `amt` `amount` DECIMAL(10,2) NOT NULL, RENAME COLUMN x TO y, DROP COLUMN z,"
                  " DROP INDEX i, RENAME TO orders2;\n"
                  "ALTER TABLE t RENAME INDEX a TO b;\nDROP TABLE IF EXISTS q, `r`;\n"
                  "SELECT 'RENAME TABLE fake TO fake2';")
        self.assertEqual(h["tables"], [("a", "b"), ("c", "d"), ("orders", "orders2")])
        self.assertEqual(h["columns"], [("orders", "amt", "amount"), ("orders", "x", "y")])
        self.assertEqual(h["dropped_tables"], {"q", "r"})
        self.assertEqual(h["dropped_columns"], {("orders", "z")})

    def test_oracle_statements_fold_to_upper(self):
        h = hints('ALTER TABLE emp RENAME COLUMN sal TO salary; RENAME dept TO department; ALTER TABLE "Emp" DROP (a, b);',
                  fold=str.upper)
        self.assertEqual(h["tables"], [("DEPT", "DEPARTMENT")])
        self.assertEqual(h["columns"], [("EMP", "SAL", "SALARY")])
        self.assertEqual(h["dropped_columns"], {("Emp", "A"), ("Emp", "B")})

    def test_idmap_follows_renames_and_breaks_on_drop_recreate(self):
        m = IdMap(case_insensitive_columns=True)
        first = ids(m.assign(doc(("a", ["id", "x"]), ("b", ["id"]))))
        second = ids(m.assign(doc(("a", ["id", "x"]), ("c", ["ID", "y"])),
                              hints("RENAME TABLE b TO c; ALTER TABLE c ADD y int; ALTER TABLE a DROP COLUMN x, ADD x int;")))
        self.assertEqual(second["c"][0], first["b"][0])
        self.assertEqual(second["c"][1]["ID"], first["b"][1]["id"])
        self.assertEqual(second["a"][0], first["a"][0])
        self.assertNotEqual(second["a"][1]["x"], first["a"][1]["x"])
        third = ids(m.assign(doc(("a", ["id", "x"]), ("b", ["id"]), ("c", ["ID", "y"])), hints("CREATE TABLE b (id int);")))
        self.assertNotIn(third["b"][0], {v[0] for v in second.values()})

    def test_hints_after_client_directives_and_other_schema(self):
        h = hints("DELIMITER $$\nCREATE PROCEDURE p1() BEGIN SELECT 1; END$$\nCREATE PROCEDURE p2() BEGIN SELECT 2; END$$\n"
                  "DELIMITER ;\nRENAME TABLE ok1 TO ok2; RENAME TABLE other.a TO other.b; ALTER TABLE replay.c RENAME COLUMN x TO y;",
                  dialect="mysql", schema="replay")
        self.assertEqual((h["tables"], h["columns"]), ([("ok1", "ok2")], [("c", "x", "y")]))
        h = hints("BEGIN NULL; END;\n/\nRENAME dept TO department;\nSET DEFINE OFF\nALTER TABLE emp RENAME COLUMN a TO b;",
                  str.upper, "oracle", "REPLAY")
        self.assertEqual((h["tables"], h["columns"]), ([("DEPT", "DEPARTMENT")], [("EMP", "A", "B")]))

    def test_rename_then_reuse_old_name(self):
        m = IdMap()
        first = ids(m.assign(doc(("a", ["id"]))))
        second = ids(m.assign(doc(("a", ["id"]), ("b", ["id"])), hints("RENAME TABLE a TO b; CREATE TABLE a (id int);")))
        self.assertEqual(second["b"][0], first["a"][0])
        self.assertNotEqual(second["a"][0], first["a"][0])


class SecretTest(unittest.TestCase):
    def test_password_forms_by_dialect(self):
        cases = [
            ("CREATE USER 'u'@'h' IDENTIFIED WITH caching_sha2_password BY 'sec1';", "mysql", ["sec1"]),
            ("ALTER USER 'u'@'h' IDENTIFIED BY 'new2' REPLACE 'old3';", "mysql", ["new2", "old3"]),
            ("SET PASSWORD FOR 'u'@'h' = 'sec4';", "mysql", ["sec4"]),
            ("SET PASSWORD = PASSWORD('sec5');", "mysql", ["sec5"]),
            ('CREATE USER u IDENTIFIED BY "sec6";', "mysql", ["sec6"]),
            ("CREATE USER m IDENTIFIED WITH mysql_native_password AS '*HASH7';", "mysql", ["*HASH7"]),
            ("CREATE USER app IDENTIFIED BY sec8;", "oracle", ["sec8"]),
            ('CREATE USER x IDENTIFIED BY "Sec9";', "oracle", ["Sec9"]),
            ("CREATE LOGIN l WITH PASSWORD = 'sec10';", "postgres", ["sec10"]),
            ("# don't forget\nCREATE USER u IDENTIFIED BY 'sec11';", "mysql", ["sec11"]),
            ("PROMPT Don't forget\nCREATE USER a IDENTIFIED BY sec12;", "oracle", ["sec12"]),
            ("REM it's user\nCREATE USER a IDENTIFIED BY \"Sec13\";", "oracle", ["Sec13"]),
            ("CHANGE MASTER TO MASTER_PASSWORD='sec14';", "mysql", ["sec14"]),
            ("ALTER USER a IDENTIFIED BY sec15 REPLACE sec16;", "oracle", ["sec15", "sec16"]),
        ]
        for sql, dialect, values in cases:
            out, found = mask_secrets(sql, dialect)
            for v in values:
                self.assertNotIn(v, out, sql)
                self.assertIn(v, found, sql)

    def test_mysql_backslash_quote_does_not_leak(self):
        out, found = mask_secrets("CREATE USER u IDENTIFIED BY 'pa\\'ss;word'; CREATE TABLE t (a int);", "mysql")
        self.assertEqual(out, "CREATE USER u IDENTIFIED BY '********'; CREATE TABLE t (a int);")
        self.assertIn("pa'ss;word", found)

    def test_ordinary_literals_are_kept(self):
        sql = "select replace('abc','a','b'); INSERT INTO t VALUES ('keep');"
        self.assertEqual(mask_secrets(sql), (sql, []))


class ScriptSplitTest(unittest.TestCase):
    def test_oracle_terminators_and_sqlplus_lines(self):
        script, warnings = oracle_script(
            "SET DEFINE OFF\nPROMPT hi\nCREATE TABLE a (x NUMBER); CREATE TABLE b (y VARCHAR2(9) DEFAULT 'a;b')\n/\n"
            "CREATE OR REPLACE TRIGGER trg BEFORE INSERT ON a FOR EACH ROW\nBEGIN\n  :NEW.x := 1;\nEND;\n/\n"
            "EXEC dbms_output.put_line('x');\nCREATE TABLE c (id NUMBER)")
        self.assertEqual(script.splitlines(), [
            "CREATE TABLE a (x NUMBER);", "CREATE TABLE b (y VARCHAR2(9) DEFAULT 'a;b');",
            "CREATE OR REPLACE TRIGGER trg BEFORE INSERT ON a FOR EACH ROW", "BEGIN", "  :NEW.x := 1;", "END;", "/",
            "BEGIN dbms_output.put_line('x'); END;", "/", "CREATE TABLE c (id NUMBER);"])
        self.assertEqual(warnings, [])

    def test_oracle_missing_slash_is_reported_without_text(self):
        script, warnings = oracle_script("CREATE TRIGGER t BEFORE INSERT ON a FOR EACH ROW\nBEGIN\n  NULL;\nEND;\nCREATE TABLE z (id NUMBER);")
        self.assertTrue(script.rstrip().endswith("/"))
        self.assertEqual(len(warnings), 1)
        _, warnings = oracle_script("BEGIN\n NULL;\nEND;\nALTER USER app IDENTIFIED BY Sup3rSecret;\n")
        self.assertNotIn("Sup3rSecret", " ".join(warnings))
        self.assertEqual(len(oracle_script("CREATE TYPE pt AS OBJECT (x NUMBER);\nCREATE TABLE t1 (a pt);")[1]), 1)

    def test_oracle_blocks_after_comments_and_wrapped_heads(self):
        for src in ("/* hdr */ CREATE PROCEDURE p1 IS BEGIN NULL; END;\n/\nCREATE TABLE h1(a int);",
                    "CREATE OR REPLACE\nPROCEDURE p3 IS\nBEGIN NULL; END;\n/\nCREATE TABLE n1(a int);",
                    "INSERT INTO q0 VALUES (q'[it's; fine]');\nCREATE PROCEDURE p2 IS BEGIN NULL; END;\n/\nCREATE TABLE q1(a int);"):
            script, warnings = oracle_script(src)
            self.assertIn("/", script.splitlines(), script)
            self.assertTrue(script.rstrip().endswith("(a int);"), script)
            self.assertEqual(warnings, [])
        self.assertIn("q'[it's; fine]'", oracle_script("INSERT INTO q0 VALUES (q'[it's; fine]');")[0])

    def test_no_false_missing_slash_warning_for_end_if(self):
        self.assertEqual(oracle_script("BEGIN\n IF x THEN\n  NULL;\n END IF;\n INSERT INTO e0 VALUES (1);\nEND;\n/")[1], [])
        self.assertEqual(oracle_script("BEGIN\n  BEGIN\n    NULL;\n  END;\n  UPDATE t2 SET id = 2;\nEND;\n/")[1], [])
        self.assertEqual(len(oracle_script("CREATE OR REPLACE\nTYPE pt AS OBJECT (x NUMBER);\nCREATE TABLE t1 (a pt);")[1]), 1)

    def test_rem_prompt_words_inside_statements_are_code(self):
        src = ("CREATE TABLE a (\n  id NUMBER,\n  REMARK VARCHAR2(5));\nCREATE OR REPLACE TRIGGER t BEFORE INSERT ON a FOR EACH ROW\n"
               "BEGIN\n  :NEW.id := 1;\nEND;\n/\nCREATE TABLE c (x NUMBER);")
        lines = oracle_script(src)[0].splitlines()
        self.assertEqual(lines[2], "  REMARK VARCHAR2(5));")
        self.assertEqual(lines[-2:], ["/", "CREATE TABLE c (x NUMBER);"])
        h = hints("CREATE TABLE a (\n id NUMBER,\n  REMARK VARCHAR2(5));\nALTER TABLE a RENAME TO b;", str.upper, "oracle")
        self.assertEqual(h["tables"], [("A", "B")])
        from sqltext import tokens
        body = ("CREATE OR REPLACE TRIGGER t BEFORE INSERT ON a FOR EACH ROW\nBEGIN\n  :NEW.id := 1;\n  REMARK := 'it''s;';\nEND;\n/\n"
                "PROMPT it's done\nCREATE TABLE z (a NUMBER);")
        kinds = [k for k, _ in tokens(body, "oracle")]
        self.assertEqual(kinds.count("comment"), 1)
        self.assertIn("  REMARK := 'it''s;' \nEND ;", normalize(body, "oracle"))
        self.assertEqual(len(oracle_script("CREATE TYPE t AS OBJECT (\n a NUMBER\n);\nINSERT INTO x VALUES (1);\n")[1]), 1)

    def test_routine_bodies_are_one_statement(self):
        mysql = "DELIMITER //\nCREATE TRIGGER t AFTER UPDATE ON a FOR EACH ROW BEGIN INSERT INTO b VALUES (1); END//\nDELIMITER ;\n"
        self.assertFalse(analyze(mysql, "mysql")[0])
        self.assertFalse(analyze("CREATE TRIGGER t BEFORE INSERT ON a FOR EACH ROW\nBEGIN\n  INSERT INTO b VALUES (1);\nEND;\n/\n", "oracle")[0])
        self.assertTrue(analyze(mysql + "INSERT INTO a VALUES (1);", "mysql")[0])
        self.assertEqual(normalize("DELIMITER $$\nCREATE PROCEDURE p() BEGIN SELECT 1; END$$\nDELIMITER ;\nSELECT 2;", "mysql"),
                         "CREATE PROCEDURE p() BEGIN SELECT 1  END;\nSELECT 2;")


class DetectEngineTest(unittest.TestCase):
    def test_mysql_flyway_repo(self):
        with tempfile.TemporaryDirectory() as d:
            mig = Path(d, "src", "main", "resources", "db", "migration")
            mig.mkdir(parents=True)
            for f in (ROOT / "samples" / "mysql").glob("V*.sql"):
                shutil.copy(f, mig)
            Path(d, "application.yml").write_text("spring:\n  datasource:\n    url: jdbc:mysql://db:3306/shop\n")
            Path(d, "docker-compose.yml").write_text("services:\n  db:\n    image: mariadb:10.11\n")
            r = json.loads(subprocess.run([sys.executable, str(ROOT / "scripts" / "detect.py"), d], check=True,
                                          capture_output=True, text=True).stdout)
            self.assertEqual((r["format"], r["db"], r["supported"]), ("flyway", "mysql", True))
            self.assertIn("mariadb:10.11", r["suggested_images"])
            self.assertTrue(any("--db mariadb" in w for w in r["warnings"]))

    def test_sqlserver_is_not_supported(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "db").mkdir()
            Path(d, "db", "V1__a.sql").write_text("CREATE TABLE a (id int);")
            Path(d, "application.properties").write_text("spring.datasource.url=jdbc:sqlserver://h;databaseName=x\n")
            r = json.loads(subprocess.run([sys.executable, str(ROOT / "scripts" / "detect.py"), d], check=True,
                                          capture_output=True, text=True).stdout)
            self.assertFalse(r["supported"])
            self.assertIsNone(r["db"])

    def test_prisma_provider_must_match_engine(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "20240101000000_a").mkdir()
            Path(d, "20240101000000_a", "migration.sql").write_text("select 1;")
            Path(d, "migration_lock.toml").write_text('provider = "mysql"\n')
            self.assertEqual(len(find_prisma(Path(d), "mariadb")[0]), 1)
            with self.assertRaises(SystemExit):
                find_prisma(Path(d), "postgres")


def replay(sample, db, out):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "replay.py"), str(ROOT / "samples" / sample), "--db", db,
                           "--out", str(out)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(proc.stdout + proc.stderr)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "timeline.py"), str(out)], check=True, capture_output=True)
    return json.loads((out / "steps.json").read_text()), json.loads((out / "events.json").read_text())


def renames(events):
    return sorted((e["kind"], e["table"], e["before"], e["after"], e.get("confidence")) for e in events
                  if e["kind"] in ("table_renamed", "column_renamed"))


class MySQLFamily:
    db = None
    image = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.meta, cls.events = replay("mysql", cls.db, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_all_steps_applied_with_delimiter_trigger(self):
        self.assertEqual([r["status"] for r in self.meta["steps"]], ["applied"] * 5)
        self.assertEqual(self.meta["engine"], self.db)
        self.assertTrue(self.meta["steps"][3]["dml"])

    def test_renames_confirmed_by_statements(self):
        self.assertEqual(renames(self.events), [
            ("column_renamed", "member", "nm", "name", "confirmed"),
            ("column_renamed", "purchase", "amount", "total_amount", "confirmed"),
            ("table_renamed", "purchase", "orders", "purchase", "confirmed"),
        ])

    def test_ddl_from_show_create(self):
        snap = json.loads((self.tmp / "snapshots" / "005.json").read_text())
        member = next(t for t in snap["tables"] if t["name"] == "member")
        self.assertIn("CREATE TABLE `member`", member["ddl"])
        self.assertNotIn("AUTO_INCREMENT=", member["ddl"])
        self.assertEqual(snap["schema"][0]["text"], "SET FOREIGN_KEY_CHECKS = 0;")
        self.assertEqual([c["name"] for c in member["columns"]], ["id", "email", "name", "joined_at"])
        self.assertEqual(next(c for c in member["columns"] if c["name"] == "id")["identity"], "auto_increment")


@unittest.skipUnless(has_image("mysql:8.4"), "mysql:8.4 이미지 필요")
class MySQLReplayTest(MySQLFamily, unittest.TestCase):
    db = "mysql"


@unittest.skipUnless(has_image("mariadb:11.4"), "mariadb:11.4 이미지 필요")
class MariaDBReplayTest(MySQLFamily, unittest.TestCase):
    db = "mariadb"


@unittest.skipUnless(has_image("gvenzl/oracle-free:23-slim"), "gvenzl/oracle-free:23-slim 이미지 필요")
class OracleReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        nets = lambda: set(subprocess.run(["docker", "network", "ls", "--filter", "name=schema-timeline-", "--format", "{{.Name}}"],
                                          capture_output=True, text=True).stdout.split())
        before = nets()
        cls.meta, cls.events = replay("oracle", "oracle", cls.tmp)
        cls.leftover = nets() - before

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_applied_and_network_removed(self):
        self.assertEqual([r["status"] for r in self.meta["steps"]], ["applied"] * 3)
        self.assertEqual(self.leftover, set())

    def test_renames_confirmed(self):
        self.assertEqual(renames(self.events), [
            ("column_renamed", "EMP", "SAL", "SALARY", "confirmed"),
            ("table_renamed", "DEPARTMENT", "DEPT", "DEPARTMENT", "confirmed"),
        ])

    def test_catalog_shape(self):
        snap = json.loads((self.tmp / "snapshots" / "003.json").read_text())
        emp = next(t for t in snap["tables"] if t["name"] == "EMP")
        cols = {c["name"]: c for c in emp["columns"]}
        self.assertEqual(cols["BONUS"]["generated"], "virtual")
        self.assertEqual(cols["SALARY"]["comment"], "월급")
        self.assertNotIn("HIRED", cols)
        self.assertFalse(any(k["kind"] == "c" and "IS NOT NULL" in k["definition"] for k in emp["constraints"]))
        fk = next(k for k in emp["constraints"] if k["kind"] == "f")
        self.assertEqual(fk["ref_table"], "DEPARTMENT")
        dept = next(t for t in snap["tables"] if t["name"] == "DEPARTMENT")
        self.assertEqual(next(c for c in dept["columns"] if c["name"] == "ID")["identity"], "d")
        self.assertEqual([o["name"] for o in snap["schema"]][:2], ["DEPARTMENT", "EMP"])


def replay_dir(files, db, out):
    """임시 이력 디렉터리를 재생한다. 실패해도 steps.json 을 돌려준다."""
    src = out / "src"
    src.mkdir(parents=True)
    for name, text in files.items():
        (src / name).write_text(text)
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "replay.py"), str(src), "--db", db, "--out", str(out / "r")],
                          capture_output=True, text=True)
    return proc, json.loads((out / "r" / "steps.json").read_text())


@unittest.skipUnless(has_image("gvenzl/oracle-free:23-slim"), "gvenzl/oracle-free:23-slim 이미지 필요")
class OracleEdgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.proc, cls.meta = replay_dir({
            "V1__a.sql": "CREATE TABLE t1 (x NUMBER, y NUMBER, z NUMBER);\nALTER TABLE t1 ADD CHECK (x > 0 AND z IS NOT NULL);\n"
                         "ALTER TABLE t1 ADD CHECK (y IS NOT NULL);\nCREATE TABLE t_noterm (id NUMBER)",
            "V2__b.sql": "CREATE OR REPLACE TRIGGER trg BEFORE INSERT ON t1 FOR EACH ROW\nBEGIN\n  NULL;\nEND;\n"
                         "CREATE TABLE t_after (id NUMBER);\n",
            "V3__c.sql": "/* hdr */ CREATE PROCEDURE p1 IS BEGIN NULL; END;\n/\nCREATE TABLE h1 (a NUMBER);\n"
                         "CREATE OR REPLACE\nPROCEDURE p3 IS\nBEGIN NULL; END;\n/\nCREATE TABLE n1 (a NUMBER);\n"
                         "INSERT INTO h1 VALUES (LENGTH(q'[it's; fine]'));\nCREATE PROCEDURE p2 IS BEGIN NULL; END;\n/\n"
                         "CREATE TABLE q1 (a NUMBER);\nPROMPT Don't stop\nCREATE TABLE q2 (a NUMBER);\n"
                         "CREATE TABLE r1 (\n  id NUMBER,\n  REMARK VARCHAR2(5));\n"
                         "CREATE OR REPLACE TRIGGER rt BEFORE INSERT ON r1 FOR EACH ROW\nBEGIN\n  :NEW.id := 1;\nEND;\n/\n"
                         "CREATE TABLE r2 (x NUMBER)\n",
            "V4__d.sql": "CREATE USER other_u IDENTIFIED BY Sec_bare1;\n",
        }, "oracle", cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_unnamed_checks_kept_and_unterminated_statement_applied(self):
        snap = json.loads((self.tmp / "r" / "snapshots" / "001.json").read_text())
        names = [t["name"] for t in snap["tables"]]
        self.assertIn("T_NOTERM", names)
        t1 = next(t for t in snap["tables"] if t["name"] == "T1")
        checks = sorted(k["definition"] for k in t1["constraints"] if k["kind"] == "c")
        self.assertEqual(checks, ["CHECK (x > 0 AND z IS NOT NULL)", "CHECK (y IS NOT NULL)"])

    def test_blocks_after_comments_wrapped_heads_and_q_quotes_apply(self):
        snap = json.loads((self.tmp / "r" / "snapshots" / "003.json").read_text())
        self.assertTrue({"H1", "N1", "Q1", "Q2", "R1", "R2"} <= {t["name"] for t in snap["tables"]})
        r1 = next(t for t in snap["tables"] if t["name"] == "R1")
        self.assertEqual([c["name"] for c in r1["columns"]], ["ID", "REMARK"])
        self.assertEqual(self.meta["steps"][2]["warnings"], [])

    def test_missing_slash_warns_and_password_hidden(self):
        warnings = " ".join(self.meta["steps"][1]["warnings"])
        self.assertIn("/ 가 없어", warnings)
        self.assertIn("컴파일 오류", warnings)
        self.assertEqual([r["status"] for r in self.meta["steps"]], ["applied", "applied", "applied", "failed"])
        self.assertIn("ORA-01031", self.meta["steps"][3]["error"])
        self.assertNotIn("created.", self.meta["steps"][3]["error"])
        self.assertNotIn("Sec_bare1", json.dumps(self.meta, ensure_ascii=False))
        self.assertNotIn("Sec_bare1", self.proc.stdout + self.proc.stderr)


class MySQLEdge:
    db = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.proc, cls.meta = replay_dir({
            "V1__a.sql": "CREATE TABLE a (id INT PRIMARY KEY, nm VARCHAR(10), f BIT(1) DEFAULT b'1', KEY ix_nm (nm DESC));",
            "V2__b.sql": "ALTER TABLE a DROP INDEX ix_nm, ADD INDEX ix_nm (nm);",
            "V3__c.sql": "CREATE USER 'u2'@'%' IDENTIFIED WITH caching_sha2_password BY 'sec_b2';",
        }, cls.db, cls.tmp)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "timeline.py"), str(cls.tmp / "r")], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_desc_index_change_and_bit_default(self):
        snap = json.loads((self.tmp / "r" / "snapshots" / "001.json").read_text())
        a = snap["tables"][0]
        self.assertIn("DESC", a["indexes"][0]["definition"])
        self.assertEqual(next(c for c in a["columns"] if c["name"] == "f")["default"], "b'1'")
        events = json.loads((self.tmp / "r" / "events.json").read_text())
        self.assertTrue(any(e["step"] == 2 and e["kind"].startswith("index_") for e in events))

    def test_password_not_in_outputs(self):
        self.assertNotIn("sec_b2", json.dumps(self.meta, ensure_ascii=False))
        self.assertNotIn("sec_b2", self.proc.stdout + self.proc.stderr)


@unittest.skipUnless(has_image("mysql:8.4"), "mysql:8.4 이미지 필요")
class MySQLEdgeTest(MySQLEdge, unittest.TestCase):
    db = "mysql"


@unittest.skipUnless(has_image("mariadb:11.4"), "mariadb:11.4 이미지 필요")
class MariaDBEdgeTest(MySQLEdge, unittest.TestCase):
    db = "mariadb"


@unittest.skipUnless(has_image("mysql:8.4"), "mysql:8.4 이미지 필요")
class CompareMySQLTest(unittest.TestCase):
    def test_snapshot_two_files(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.sql"), Path(d, "b.sql")
            a.write_text("CREATE TABLE t (id INT PRIMARY KEY, v VARCHAR(10));")
            b.write_text("CREATE TABLE t (id INT PRIMARY KEY, v VARCHAR(20) NOT NULL, w INT);")
            subprocess.run([sys.executable, str(ROOT / "scripts" / "snapshot.py"), str(a), str(b), "--db", "mysql",
                            "--out", str(Path(d, "s"))], check=True, capture_output=True)
            s0, s1 = (json.loads(Path(d, "s", f"0{i}.json").read_text()) for i in (0, 1))
            self.assertEqual((s0["engine"], s1["tables"][0]["columns"][1]["type"]), ("mysql", "varchar(20)"))


if __name__ == "__main__":
    unittest.main()
