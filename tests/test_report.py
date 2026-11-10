"""scripts/report.mjs 검증: JSON 모양, 종료 코드, Markdown, 그리고 비교 화면 collectRisks() 와의 일치.

일치 검사는 node 와 헤드리스 Chrome, 그리고 out/demo·out/oracle_retail 출력이 있을 때만 돈다.
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
REPORT = ROOT / "scripts" / "report.mjs"
CHROME = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"]
NODE = shutil.which("node")


def run(*args):
    env = {k: v for k, v in os.environ.items() if k != "SCHEMA_TIMELINE_LANG"}
    return subprocess.run([NODE, str(REPORT), *map(str, args)], capture_output=True, text=True, timeout=60, env=env)


def table(name, status="same", columns=()):
    return {"name": name, "status": status, "comment": {"before": None, "after": None}, "ddl": {}, "columns": list(columns), "constraints": [], "indexes": []}


def build_output(root, steps, diffs):
    """steps 와 diffs 로 최소한의 타임라인 출력 디렉터리를 만든다."""
    (root / "diffs").mkdir(parents=True)
    (root / "steps.json").write_text(json.dumps({"source": "mini", "engine": "postgres", "steps": steps}))
    for i, d in enumerate(diffs, 1):
        (root / "diffs" / f"{i:03d}.json").write_text(json.dumps({"before": {}, "after": {}, "views": [], "events": [], "engine": "postgres", **d}))


def step(i, status="applied", **extra):
    return {"index": i, "version": str(i), "label": f"V{i}", "description": "x", "file": f"V{i}__x.sql", "status": status,
            "error": None, "objects": {"added": [], "dropped": [], "changed": [], "invalid": []}, "event_count": 0, "data_loss": False, **extra}


@unittest.skipUnless(NODE, "node 가 없습니다")
class ReportCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="report-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def mini(self, name, steps, diffs):
        root = self.tmp / name
        build_output(root, steps, diffs)
        return root

    def test_json_shape_and_danger_exit(self):
        out = self.mini("danger", [step(1), step(2, data_loss=True)], [
            {"tables": [table("a", "added")]},
            {"tables": [table("a", "dropped", [{}]), table("b")]}])
        r = run(out, "--lang", "en")
        self.assertEqual(r.returncode, 4, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual(set(rep), {"source", "engine", "lang", "steps", "totals", "failed_steps"})
        self.assertEqual((rep["source"], rep["engine"], rep["lang"]), ("mini", "postgres", "en"))
        self.assertEqual(rep["failed_steps"], [])
        self.assertEqual([s["index"] for s in rep["steps"]], [1, 2])
        for s in rep["steps"]:
            self.assertEqual(set(s), {"index", "label", "file", "status", "data_loss", "risks", "hints", "checks"})
        self.assertEqual(rep["steps"][0]["risks"], [])
        risk = rep["steps"][1]["risks"][0]
        self.assertEqual((risk["level"], risk["what"], risk["lead"]), ("danger", "a", "Whole table dropped"))
        self.assertEqual(risk["check"], {"sql": "SELECT COUNT(*) FROM a;", "expect": "affected"})
        self.assertIn("SELECT COUNT(*) FROM a;", rep["steps"][1]["checks"])
        self.assertEqual(rep["totals"], {"danger": 1, "attention": 0, "hint": 0})

    def test_fail_on_levels(self):
        out = self.mini("attn", [step(1)], [{"tables": [table("a", "changed", [{"name": "x", "status": "added", "changes": [], "before": None,
                                           "after": {"type": "integer", "nullable": False, "default": None, "identity": False}}])]}])
        self.assertEqual(run(out).returncode, 0)
        self.assertEqual(run(out, "--fail-on", "attention").returncode, 4)
        self.assertEqual(run(out, "--fail-on", "none").returncode, 0)

    def test_fail_on_none_with_danger(self):
        out = self.mini("danger", [step(1)], [{"tables": [table("a", "dropped", [{}])]}])
        self.assertEqual(run(out).returncode, 4)
        self.assertEqual(run(out, "--fail-on", "none").returncode, 0)

    def test_failed_step_exit_1(self):
        out = self.mini("failed", [step(1), step(2, "failed", error="ERROR: relation \"nope\" does not exist"), step(3, "not_run")],
                        [{"tables": [table("a", "dropped", [{}])]}])
        r = run(out)
        self.assertEqual(r.returncode, 1, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual([f["index"] for f in rep["failed_steps"]], [2])
        self.assertIn("nope", rep["failed_steps"][0]["error"])
        self.assertEqual([s["status"] for s in rep["steps"]], ["applied", "failed", "not_run"])
        md = run(out, "--format", "md", "--lang", "en").stdout
        self.assertLess(md.index("Replay failed"), md.index("### V1"))

    def test_usage_errors_exit_2(self):
        out = self.mini("ok", [step(1)], [{"tables": []}])
        for args in ([], [out, "--format", "xml"], [out, "--step", "9"], [out, "--from", "1"], [out, "--bogus"], [self.tmp / "nope"],
                     [out, "--fail-on", "x"], [out, "--lang", "fr"], [out, "--step", "1", "--all"], [out, "--from", "1", "--to", "5"]):
            self.assertEqual(run(*args).returncode, 2, args)

    def test_usage_error_language(self):
        out = self.mini("range", [step(1)], [{"tables": []}])
        en = run(out, "--step", "9", "--lang", "en")
        self.assertEqual(en.returncode, 2)
        self.assertIn("--step must be between 1 and 1", en.stderr)
        ko = run(out, "--step", "9", "--lang", "ko")
        self.assertEqual(ko.returncode, 2)
        self.assertIn("--step 은 1 ~ 1 사이여야 합니다", ko.stderr)
        bad = run(out, "--lang", "en", "--bogus")
        self.assertIn("Unknown option: --bogus", bad.stderr)

    def test_help_exit_0(self):
        for flag, lang, text in (("--help", "en", "Usage:"), ("-h", "ko", "사용법:")):
            r = run(flag, "--lang", lang)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("--fail-on", r.stdout)
            self.assertIn(text, r.stdout)

    def test_repeated_flag_exit_2(self):
        out = self.mini("repeat", [step(1)], [{"tables": []}])
        for args in ([out, "--step", "1", "--step", "1"], [out, "--format", "json", "--format", "md"], [out, "--all", "--all"]):
            r = run(*args, "--lang", "en")
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("more than once", r.stderr)
        r = run(out, "--step", "1", "--step", "1", "--lang", "ko")
        self.assertIn("여러 번 지정", r.stderr)

    def test_lang_precedence(self):
        out = self.mini("lang", [step(1)], [{"tables": [table("a", "dropped", [{}])]}])
        env = {**os.environ, "SCHEMA_TIMELINE_LANG": "ko"}
        r = subprocess.run([NODE, str(REPORT), str(out)], capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(r.stdout)["lang"], "ko")
        r = subprocess.run([NODE, str(REPORT), str(out), "--lang", "en"], capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(r.stdout)["lang"], "en")
        env = {k: v for k, v in os.environ.items() if k != "SCHEMA_TIMELINE_LANG"} | {"LC_ALL": "ko_KR.UTF-8"}
        r = subprocess.run([NODE, str(REPORT), str(out)], capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(r.stdout)["lang"], "ko")

    def test_markdown_clean_output(self):
        out = self.mini("clean", [step(1)], [{"tables": []}])
        r = run(out, "--format", "md", "--lang", "en")
        self.assertEqual(r.returncode, 0)
        self.assertIn("No changes need review.", r.stdout)
        self.assertNotIn("###", r.stdout)


DEMO = ROOT / "out" / "demo"


@unittest.skipUnless(NODE and (DEMO / "steps.json").exists(), "node 나 out/demo 가 없습니다")
class ReportDemoTest(unittest.TestCase):
    def test_demo_v7_danger_and_markdown(self):
        r = run(DEMO, "--lang", "en")
        self.assertEqual(r.returncode, 4)
        rep = json.loads(r.stdout)
        v7 = next(s for s in rep["steps"] if s["label"] == "V7")
        self.assertTrue(any(x["level"] == "danger" for x in v7["risks"]))
        self.assertEqual(run(DEMO, "--fail-on", "none").returncode, 0)
        md = run(DEMO, "--format", "md", "--lang", "en").stdout
        self.assertTrue(md.startswith("## Schema change review: demo"))
        self.assertIn("### V7 `V7__drop_legacy.sql`", md)
        self.assertNotIn("### V1 ", md)
        self.assertIn("- **Danger** Whole table dropped `legacy_coupons`", md)
        ko = run(DEMO, "--format", "md", "--lang", "ko").stdout
        self.assertIn("스키마 변경 검토", ko)
        self.assertIn("**위험**", ko)
        self.assertEqual(run(DEMO, "--from", "1", "--to", "8").returncode, 4)


@unittest.skipUnless(NODE and next((p for p in CHROME if Path(p).exists()), None), "node 나 Chrome 이 없습니다")
class ReportParityTest(unittest.TestCase):
    """report.mjs 의 위험 목록이 렌더된 비교 화면의 collectRisks() 와 같은지 단계마다·구간마다 확인한다."""

    def check(self, out, lang):
        out = ROOT / "out" / out
        if not (out / "steps.json").exists():
            self.skipTest(f"{out} 가 없습니다")
        meta = json.loads((out / "steps.json").read_text())
        applied = sum(s["status"] == "applied" for s in meta["steps"])
        with tempfile.TemporaryDirectory(prefix="report-parity-") as tmp:
            html = Path(tmp) / "t.html"
            subprocess.run([sys.executable, str(ROOT / "scripts" / "render_timeline.py"), str(out), "--out", str(html), "--lang", lang],
                           check=True, capture_output=True, timeout=120)
            scenes = [str(i) for i in range(1, applied + 1)] + [f"1-{n}" for n in range(2, applied + 1)]
            chrome = next(p for p in CHROME if Path(p).exists())
            r = subprocess.run([NODE, str(ROOT / "tests" / "cdp_risks.mjs"), str(html), *scenes], capture_output=True, text=True, timeout=600,
                               env={**os.environ, "CHROME_PATH": chrome})
            self.assertEqual(r.returncode, 0, r.stderr)
            page = json.loads(r.stdout)
        norm = lambda r: (r["level"], r.get("kind"), r.get("what"), r.get("lead"), r.get("why"))
        for sc in scenes:
            rep = run(out, "--lang", lang, *(["--from", *sc.split("-")[:1], "--to", sc.split("-")[1]] if "-" in sc else ["--step", sc]))
            self.assertIn(rep.returncode, (0, 4), rep.stderr)
            got = [norm(x) for x in json.loads(rep.stdout)["steps"][0]["risks"]]
            want = [norm(x) for x in page[sc]["risks"]]
            self.assertEqual(got, want, f"{out.name} {sc}")

    def test_demo_ko(self):
        self.check("demo", "ko")

    def test_oracle_retail_en(self):
        self.check("oracle_retail", "en")


if __name__ == "__main__":
    unittest.main()
