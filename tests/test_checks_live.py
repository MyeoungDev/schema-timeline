"""배포 전 확인 쿼리(templates/risks.js)가 실제 엔진에서 오류 없이 도는지 검증한다.

위험 변경을 모은 이력(V1 기준 스키마, V2 위험 변경)을 재생해 report.mjs 로 V2 의 확인 쿼리를 뽑고,
V1 다음에 그 쿼리를 V2 로 둔 이력을 다시 재생해 두 단계가 모두 적용되는지 본다. 엔진 이미지가 로컬에 있을 때만 돈다.
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


def has_image(image):
    if not shutil.which("docker") or not shutil.which("node") or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return False
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0


def replay(files, db, out):
    """files 를 이력 디렉터리로 써서 재생하고 timeline.py 까지 돌린다. steps.json 을 돌려준다."""
    src = out / "src"
    src.mkdir(parents=True)
    for name, text in files.items():
        (src / name).write_text(text)
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "replay.py"), str(src), "--db", db, "--out", str(out / "r")],
                          capture_output=True, text=True)
    meta = json.loads((out / "r" / "steps.json").read_text())
    if proc.returncode == 0:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "timeline.py"), str(out / "r")], check=True, capture_output=True)
    return proc, meta


PG_V1 = """CREATE TABLE parent (id int PRIMARY KEY, code varchar(10));
CREATE TABLE child (id int, parent_id int, amount numeric(10,4), name varchar(50), note text, legacy text, "order" int, ts timestamp(6),
  "left" int, email text, deleted_at timestamp);
CREATE TABLE gone (id int);
"""
PG_V2 = """ALTER TABLE child ADD PRIMARY KEY (id);
ALTER TABLE child ADD CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES parent(id);
ALTER TABLE child ADD CONSTRAINT ck_amount CHECK (amount >= 0);
ALTER TABLE child ADD CONSTRAINT uq_name UNIQUE (name, "order");
ALTER TABLE child ALTER COLUMN amount TYPE numeric(10,2);
ALTER TABLE child ALTER COLUMN ts TYPE timestamp(0);
ALTER TABLE child ALTER COLUMN name TYPE varchar(20);
ALTER TABLE child RENAME COLUMN note TO memo;
ALTER TABLE child ALTER COLUMN memo SET NOT NULL;
ALTER TABLE child DROP COLUMN legacy;
ALTER TABLE child ADD COLUMN must int NOT NULL;
CREATE UNIQUE INDEX ux_child_memo ON child (memo);
CREATE INDEX ix_child_name ON child (name);
ALTER TABLE child ALTER COLUMN "left" SET NOT NULL;
ALTER TABLE child ADD CONSTRAINT ck_left CHECK ("left" > 0) NOT VALID;
CREATE UNIQUE INDEX ux_live_email ON child (email) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX ux_lower_email ON child (lower(email));
DROP TABLE gone;
"""
MY_V1 = """CREATE TABLE parent (id int PRIMARY KEY, code varchar(10));
CREATE TABLE child (id int NOT NULL, parent_id int, amount decimal(10,4), name varchar(50), note varchar(100), legacy text, `order` int, ts datetime(6),
  label varchar(50), tag varchar(200));
CREATE TABLE gone (id int);
"""
MY_V2 = """ALTER TABLE child ADD PRIMARY KEY (id);
ALTER TABLE child ADD CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES parent(id);
ALTER TABLE child ADD CONSTRAINT ck_amount CHECK (amount >= 0);
ALTER TABLE child ADD CONSTRAINT uq_name UNIQUE (name, `order`);
ALTER TABLE child MODIFY amount decimal(10,2);
ALTER TABLE child MODIFY ts datetime(0);
ALTER TABLE child MODIFY name varchar(20);
ALTER TABLE child RENAME COLUMN note TO memo;
ALTER TABLE child MODIFY memo varchar(100) NOT NULL;
ALTER TABLE child DROP COLUMN legacy;
ALTER TABLE child ADD COLUMN must int NOT NULL;
ALTER TABLE child MODIFY label varchar(100);
ALTER TABLE child MODIFY tag varchar(300);
CREATE INDEX ix_child_name ON child (name);
DROP TABLE gone;
"""
ORA_V1 = """CREATE TABLE parent (id NUMBER PRIMARY KEY, code VARCHAR2(10));
CREATE TABLE child (id NUMBER NOT NULL, parent_id NUMBER, amount NUMBER(10,4), name VARCHAR2(50), note VARCHAR2(100), legacy VARCHAR2(10), "LEVEL" NUMBER);
CREATE TABLE gone (id NUMBER);
"""
ORA_V2 = """ALTER TABLE child ADD CONSTRAINT pk_child PRIMARY KEY (id);
ALTER TABLE child ADD CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES parent(id);
ALTER TABLE child ADD CONSTRAINT ck_amount CHECK (amount >= 0);
ALTER TABLE child ADD CONSTRAINT uq_name UNIQUE (name, "LEVEL");
ALTER TABLE child MODIFY (amount NUMBER(8,2));
ALTER TABLE child MODIFY (name VARCHAR2(20));
ALTER TABLE child RENAME COLUMN note TO memo;
ALTER TABLE child MODIFY (memo NOT NULL);
ALTER TABLE child DROP COLUMN legacy;
ALTER TABLE child ADD (must NUMBER NOT NULL);
CREATE INDEX ix_child_name ON child (name);
DROP TABLE gone;
"""


class LiveChecks:
    db = image = v1 = v2 = None
    min_checks = 0
    min_hints = 0
    hint_whats = ()
    no_hint_whats = ()

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        proc, meta = replay({"V1__base.sql": cls.v1, "V2__risky.sql": cls.v2}, cls.db, cls.tmp / "a")
        if proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        rep = subprocess.run(["node", str(ROOT / "scripts" / "report.mjs"), str(cls.tmp / "a" / "r"), "--step", "2", "--lang", "en", "--fail-on", "none"],
                             capture_output=True, text=True, check=True)
        cls.entry = json.loads(rep.stdout)["steps"][0]
        cls.proc2, cls.meta2 = replay({"V1__base.sql": cls.v1, "V2__checks.sql": cls.entry["checks"] + "\n"}, cls.db, cls.tmp / "b")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_checks_cover_the_risky_step(self):
        with_check = [r for r in self.entry["risks"] if r.get("check")]
        self.assertGreaterEqual(len(with_check), self.min_checks, json.dumps(self.entry["risks"], indent=1))
        self.assertGreaterEqual(len(self.entry["hints"]), self.min_hints, json.dumps(self.entry["hints"], indent=1))

    def test_expected_hints(self):
        whats = [h["what"] for h in self.entry["hints"]]
        for w in self.hint_whats:
            self.assertIn(w, whats)
        for w in self.no_hint_whats:
            self.assertNotIn(w, whats)

    def test_renamed_column_is_checked_by_its_old_name(self):
        self.assertRegex(self.entry["checks"], r"(?i)\bnote\b.*IS NULL")
        self.assertNotRegex(self.entry["checks"], r"(?i)\bmemo\b.*IS NULL")

    def test_checks_run_without_errors(self):
        self.assertEqual([s["status"] for s in self.meta2["steps"]], ["applied", "applied"],
                         self.entry["checks"] + "\n\n" + json.dumps(self.meta2["steps"], indent=1, ensure_ascii=False))


@unittest.skipUnless(has_image("postgres:16-alpine"), "postgres:16-alpine 이미지와 node 필요")
class PostgresLiveChecks(LiveChecks, unittest.TestCase):
    db, image, v1, v2 = "postgres", "postgres:16-alpine", PG_V1, PG_V2
    min_checks, min_hints = 9, 8

    def test_reserved_name_is_quoted_and_odd_indexes_have_no_check(self):
        self.assertIn('"left" IS NULL', self.entry["checks"])
        self.assertIn('NOT ("left" > 0)', self.entry["checks"])
        self.assertNotIn("lower(", self.entry["checks"])
        self.assertNotIn("deleted_at", self.entry["checks"])


@unittest.skipUnless(has_image("mysql:8.4"), "mysql:8.4 이미지와 node 필요")
class MySQLLiveChecks(LiveChecks, unittest.TestCase):
    db, image, v1, v2 = "mysql", "mysql:8.4", MY_V1, MY_V2
    min_checks, min_hints = 8, 3
    hint_whats, no_hint_whats = ("child.label",), ("child.tag",)


@unittest.skipUnless(has_image("mariadb:11.4"), "mariadb:11.4 이미지와 node 필요")
class MariaDBLiveChecks(LiveChecks, unittest.TestCase):
    db, image, v1, v2 = "mariadb", "mariadb:11.4", MY_V1, MY_V2
    min_checks, min_hints = 8, 3
    hint_whats, no_hint_whats = ("child.label",), ("child.tag",)


@unittest.skipUnless(has_image("gvenzl/oracle-free:23-slim"), "gvenzl/oracle-free:23-slim 이미지와 node 필요")
class OracleLiveChecks(LiveChecks, unittest.TestCase):
    db, image, v1, v2 = "oracle", "gvenzl/oracle-free:23-slim", ORA_V1, ORA_V2
    min_checks, min_hints = 7, 5


if __name__ == "__main__":
    unittest.main()
