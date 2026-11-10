"""재생 컨테이너의 자원 제한·라벨·시간 제한·정리, --no-sql, 이력 파일 경고 검증.

docker 가 없으면 컨테이너가 필요한 테스트는 건너뛴다. 라이브 테스트는 SCHEMA_TIMELINE_RUN 으로 실행 id 를 정해
그 id 의 컨테이너만 확인한다(다른 곳에서 도는 재생과 섞이지 않는다).
"""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import engine_mysql  # noqa: E402
import engine_oracle  # noqa: E402
import snapshot  # noqa: E402
from render_timeline import omit_sql, redact_error  # noqa: E402
from sources import ignored_file_warnings, load, version_gaps  # noqa: E402

PG_IMAGE = "postgres:16-alpine"


def has_docker():
    return shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def has_image(image):
    return has_docker() and subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0


def leftovers(run_id):
    """run_id 실행이 남긴 컨테이너·네트워크 id."""
    flt = f"label=schema-timeline.run={run_id}"
    out = subprocess.run(["docker", "ps", "-a", "-q", "--filter", flt], capture_output=True, text=True).stdout.split()
    out += subprocess.run(["docker", "network", "ls", "-q", "--filter", flt], capture_output=True, text=True).stdout.split()
    return out


def replay_cmd(src, out, *extra):
    return [sys.executable, str(ROOT / "scripts" / "replay.py"), str(src), "--out", str(out), "--lang", "ko", *extra]


def write_history(directory, files):
    for name, sql in files.items():
        (Path(directory) / name).write_text(sql)


class RunArgvTest(unittest.TestCase):
    def flag(self, argv, name):
        return argv[argv.index(name) + 1]

    def test_default_limits_and_labels(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("SCHEMA_TIMELINE_MEMORY", "SCHEMA_TIMELINE_CPUS", "SCHEMA_TIMELINE_PIDS"):
                os.environ.pop(k, None)
            argv = snapshot.run_argv("img:1", "postgres")
            self.assertEqual(self.flag(argv, "--memory"), "1g")
            self.assertEqual(self.flag(argv, "--memory-swap"), "1g")
            self.assertEqual(self.flag(argv, "--cpus"), "2")
            self.assertEqual(self.flag(argv, "--pids-limit"), "512")
            self.assertEqual(self.flag(argv, "--network"), "none")
            self.assertIn("--rm", argv)
            self.assertIn("schema-timeline=1", argv)
            self.assertIn(f"schema-timeline.run={snapshot.RUN_ID}", argv)
            self.assertEqual(argv[-1], "img:1")
            oracle = snapshot.run_argv("img:1", "oracle", network="n1", rm=False)
            self.assertEqual(self.flag(oracle, "--memory"), "3g")
            self.assertEqual(self.flag(oracle, "--pids-limit"), "2048")
            self.assertNotIn("--rm", oracle)
            self.assertEqual(self.flag(oracle, "--network"), "n1")

    def test_env_overrides(self):
        env = {"SCHEMA_TIMELINE_MEMORY": "3g", "SCHEMA_TIMELINE_CPUS": "0.5", "SCHEMA_TIMELINE_PIDS": "99"}
        with mock.patch.dict(os.environ, env):
            argv = snapshot.run_argv("img:1", "oracle")
            self.assertEqual((self.flag(argv, "--memory"), self.flag(argv, "--cpus"), self.flag(argv, "--pids-limit")),
                             ("3g", "0.5", "99"))

    def test_engines_start_with_limits_and_labels(self):
        """엔진 start 가 실제로 docker run 에 넘기는 인자."""
        seen = []

        def fake_run(cmd, stdin=None, check=True):
            seen.append(cmd)
            raise RuntimeError("stop")

        for module, engine in ((snapshot, None), (engine_mysql, engine_mysql.MySQL("mysql")), (engine_mysql, engine_mysql.MySQL("mariadb")),
                               (engine_oracle, engine_oracle.Oracle())):
            seen.clear()
            with mock.patch.object(snapshot, "run", fake_run), mock.patch.object(engine_mysql, "run", fake_run), \
                    mock.patch.object(engine_oracle, "run", fake_run):
                with self.assertRaises(RuntimeError):
                    (engine.start if engine else snapshot.start_container)("img:1")
            runs = [c for c in seen if c[:2] == ["docker", "run"]] or [c for c in seen if c[:3] == ["docker", "network", "create"]]
            self.assertTrue(runs)
            if runs[0][:2] == ["docker", "run"]:
                for token in ("--memory", "--pids-limit", "--cpus"):
                    self.assertIn(token, runs[0])
            self.assertIn("schema-timeline=1", runs[0])


class StepTimeoutTest(unittest.TestCase):
    def tearDown(self):
        snapshot.set_step_timeout(None)

    def test_default_env_and_zero(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("SCHEMA_TIMELINE_STEP_TIMEOUT", None)
            self.assertEqual(snapshot.step_timeout(), 300)
        with mock.patch.dict(os.environ, {"SCHEMA_TIMELINE_STEP_TIMEOUT": "12"}):
            self.assertEqual(snapshot.step_timeout(), 12)
            snapshot.set_step_timeout(0)
            self.assertIsNone(snapshot.step_timeout())

    def test_run_step_raises_step_timeout(self):
        snapshot.set_step_timeout(1)
        with self.assertRaises(snapshot.StepTimeout) as cm:
            snapshot.run_step([sys.executable, "-c", "import time; time.sleep(30)"], "")
        self.assertEqual(cm.exception.seconds, 1)

    def test_message_is_localized(self):
        import i18n
        i18n.set_lang("en")
        try:
            self.assertEqual(snapshot.timeout_text("Step V7", 300).split(" Change")[0],
                             "Step V7 did not finish within 300 s (replay stopped). Long-running statement or lock wait?")
        finally:
            i18n.set_lang("ko")
        self.assertIn("300초 안에 끝나지 않았습니다", snapshot.timeout_text("단계 V7", 300))


@unittest.skipUnless(has_image(PG_IMAGE), "docker 와 postgres 이미지가 필요하다")
class LiveSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="st-safety-"))
        self.run_id = "t" + uuid.uuid4().hex[:10]
        self.env = dict(os.environ, SCHEMA_TIMELINE_RUN=self.run_id, SCHEMA_TIMELINE_LANG="ko")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(lambda: snapshot.cleanup(self.run_id))

    def history(self, second):
        src = self.tmp / "src"
        src.mkdir()
        write_history(src, {"V1__t.sql": "CREATE TABLE t (id int);", "V2__slow.sql": second, "V3__c.sql": "ALTER TABLE t ADD c int;"})
        return src

    def test_slow_step_times_out_and_leaves_no_container(self):
        src = self.history("SELECT pg_sleep(30);")
        began = time.time()
        proc = subprocess.run(replay_cmd(src, self.tmp / "out", "--step-timeout", "5"), capture_output=True, text=True, env=self.env)
        self.assertLess(time.time() - began, 28)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("단계 V2 이(가) 5초 안에 끝나지 않았습니다(재생 중단)", proc.stderr)
        rows = json.loads((self.tmp / "out" / "steps.json").read_text())["steps"]
        self.assertEqual([r["status"] for r in rows], ["applied", "failed", "not_run"])
        self.assertIn("5초 안에 끝나지 않았습니다", rows[1]["error"])
        self.assertEqual(leftovers(self.run_id), [])

    def test_sigterm_removes_the_container(self):
        src = self.history("SELECT pg_sleep(60);")
        p = subprocess.Popen(replay_cmd(src, self.tmp / "out", "--step-timeout", "0"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=self.env)
        try:
            for _ in range(60):
                if leftovers(self.run_id):
                    break
                time.sleep(0.5)
            self.assertTrue(leftovers(self.run_id), "컨테이너가 뜨지 않았다")
            time.sleep(6)
            p.send_signal(signal.SIGTERM)
            p.communicate(timeout=60)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(leftovers(self.run_id), [])

    def test_cleanup_command_removes_labelled_containers(self):
        cid = subprocess.run(snapshot.run_argv(PG_IMAGE, extra=["-e", "POSTGRES_PASSWORD=x"]),
                             capture_output=True, text=True, env=dict(self.env)).stdout.strip()
        self.assertTrue(cid)
        labels = subprocess.run(["docker", "inspect", "-f", "{{json .Config.Labels}}", cid], capture_output=True, text=True).stdout
        self.assertIn("schema-timeline", labels)
        self.assertEqual(snapshot.cleanup(snapshot.RUN_ID)[0], 1)
        self.assertEqual(subprocess.run(["docker", "ps", "-a", "-q", "--filter", f"id={cid}"], capture_output=True, text=True).stdout.strip(), "")

    def test_no_sql_page_has_no_statement_text(self):
        src = self.tmp / "src"
        src.mkdir()
        write_history(src, {"V1__t.sql": "CREATE TABLE t (id int, note text);",
                            "V2__seed.sql": "INSERT INTO t VALUES (1, 'zz_distinctive_seed_row_91');"})
        out = self.tmp / "out"
        proc = subprocess.run(replay_cmd(src, out), capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        scripts = ROOT / "scripts"
        subprocess.run([sys.executable, str(scripts / "timeline.py"), str(out), "--lang", "ko"], check=True, capture_output=True, env=self.env)
        full, bare = out / "full.html", out / "bare.html"
        subprocess.run([sys.executable, str(scripts / "render_timeline.py"), str(out), "--out", str(full), "--lang", "ko"],
                       check=True, capture_output=True, env=self.env)
        subprocess.run([sys.executable, str(scripts / "render_timeline.py"), str(out), "--out", str(bare), "--no-sql", "--lang", "ko"],
                       check=True, capture_output=True, env=self.env)
        self.assertIn("zz_distinctive_seed_row_91", full.read_text())
        html = bare.read_text()
        self.assertNotIn("zz_distinctive_seed_row_91", html)
        self.assertNotIn("INSERT INTO t VALUES", html)
        self.assertIn('"sql_omitted": true', html)
        self.assertIn("CREATE TABLE", html)


class NoSqlDataTest(unittest.TestCase):
    def test_omit_sql_blanks_text_and_redacts_error(self):
        steps = [{"sql": "INSERT INTO t VALUES ('a');", "error": None, "file": "V1.sql"},
                 {"sql": "SELECT 1;", "error": "ERROR:  duplicate key value violates unique constraint \"t_pkey\"\n"
                                                "DETAIL:  Key (id)=(5) already exists.\nLINE 1: INSERT INTO t VALUES ('secret')", "file": "V2.sql"}]
        out = omit_sql(steps)
        self.assertEqual([s["sql"] for s in out], ["", ""])
        self.assertEqual(out[0]["error"], None)
        self.assertEqual(out[1]["error"], 'ERROR:  duplicate key value violates unique constraint "t_pkey"')
        self.assertEqual(steps[0]["sql"], "INSERT INTO t VALUES ('a');")
        self.assertEqual(redact_error("ERROR 1062: Duplicate entry 'x@y' for key 'k'"), "ERROR 1062: Duplicate entry '…' for key '…'")


class HistoryWarningsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_ignored_files_and_gap(self):
        write_history(self.dir, {"V1__a.sql": "x", "V2__b.sql": "x", "V3__c.sql": "x", "V5__d.sql": "x",
                                 "U3__undo.sql": "x", "B2__base.sql": "x", "v2__lower.sql": "x", "V4__ext.SQL": "x",
                                 "007_numbered.sql": "x", "V9_single.sql": "x", "notes.txt": "x", "seed.sql": "x"})
        src = load("flyway", self.dir)
        text = "\n".join(src.warnings)
        for name, hint in (("U3__undo.sql", "undo"), ("B2__base.sql", "baseline"), ("v2__lower.sql", "소문자"),
                           ("V4__ext.SQL", "확장자"), ("007_numbered.sql", "V<버전>__<설명>.sql"), ("V9_single.sql", "V<버전>__<설명>.sql")):
            self.assertIn(name, text)
            self.assertIn(hint, text)
        self.assertIn("재생하지 않습니다", text)
        self.assertNotIn("seed.sql", text)
        self.assertNotIn("notes.txt", text)
        self.assertEqual([s["version"] for s in src.steps], ["1", "2", "3", "5"])
        self.assertEqual(list(src.notes), ["V4 이(가) V3 와 V5 사이에 없습니다(버전 공백)."])

    def test_gap_rules(self):
        self.assertEqual(version_gaps(["1", "2", "3"]), [])
        self.assertEqual(version_gaps(["1", "1.1", "3"]), [])
        self.assertEqual(version_gaps(["1", "2", "6"]), ["V3-V5 이(가) V2 와 V6 사이에 없습니다(버전 공백)."])
        self.assertEqual(version_gaps(["20240101", "20240301"]), [])
        self.assertEqual(version_gaps(["2", "5"], "#", 3), ["#003-#004 이(가) #002 와 #005 사이에 없습니다(버전 공백)."])

    def test_candidate_needs_a_migration_directory(self):
        write_history(self.dir, {"001_x.sql": "x"})
        self.assertEqual(ignored_file_warnings(list(self.dir.iterdir()), self.dir), [])

    def test_duplicate_version_still_stops(self):
        write_history(self.dir, {"V1__a.sql": "x", "V1.0__b.sql": "x"})
        with self.assertRaises(SystemExit):
            load("flyway", self.dir)

    def test_detect_reports_the_same(self):
        write_history(self.dir, {"V1__a.sql": "x", "V3__c.sql": "x", "U1__undo.sql": "x"})
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "detect.py"), str(self.dir), "--lang", "ko"],
                              capture_output=True, text=True)
        doc = json.loads(proc.stdout)
        self.assertTrue(any("U1__undo.sql" in w for w in doc["warnings"]))
        self.assertTrue(any("V2 이(가) V1 와 V3 사이에 없습니다" in n for n in doc["notes"]))


if __name__ == "__main__":
    unittest.main()
