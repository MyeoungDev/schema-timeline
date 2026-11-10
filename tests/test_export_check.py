"""내보내기(templates/export.js, scripts/export_erd.mjs)와 결과 점검(scripts/check_html.mjs) 검증.

node 가 없으면 건너뛰고, 점검 테스트는 Chrome 도 있어야 한다.
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
NODE = shutil.which("node")


def chrome():
    out = subprocess.run([NODE, "-e", "process.exit(0)"]).returncode == 0 if NODE else False
    cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"] + [shutil.which(n) or "" for n in ("google-chrome", "chromium")]
    return out and any(c and Path(c).exists() for c in cands)


def mermaid_of(tables):
    code = ("const {mermaidER}=require(process.argv[1]);let s='';process.stdin.on('data',c=>s+=c);"
            "process.stdin.on('end',()=>process.stdout.write(mermaidER(JSON.parse(s),{title:'t'})))")
    return subprocess.run([NODE, "-e", code, str(ROOT / "templates" / "export.js")], input=json.dumps(tables),
                          capture_output=True, text=True, check=True).stdout


AWKWARD = [
    {"name": "members", "comment": "회원", "constraints": [{"kind": "p", "columns": ["id"]}, {"kind": "u", "columns": ["email"]}],
     "columns": [{"name": "id", "type": "bigint", "nullable": False}, {"name": "email", "type": "character varying(255)", "nullable": False}]},
    {"name": "orders", "comment": None, "constraints": [
        {"kind": "p", "columns": ["id"]},
        {"kind": "f", "columns": ["member_id"], "ref_table": "members", "ref_columns": ["id"]},
        {"kind": "f", "columns": ["coupon_id"], "ref_table": "coupons", "ref_columns": ["id"]}],
     "columns": [{"name": "id", "type": "bigint", "nullable": False}, {"name": "member_id", "type": "bigint", "nullable": True},
                 {"name": "total", "type": "numeric(14,2)", "nullable": False, "comment": 'say "hi"'},
                 {"name": "coupon_id", "type": "bigint", "nullable": True}]},
    {"name": "receipts", "comment": None, "constraints": [{"kind": "f", "columns": ["order_id"], "ref_table": "orders", "ref_columns": ["id"]}],
     "indexes": [{"unique": True, "columns": ["order_id"], "definition": "CREATE UNIQUE INDEX r ON receipts (order_id)"}],
     "columns": [{"name": "order_id", "type": "bigint", "nullable": False}]},
    {"name": "주문 상세", "comment": None, "constraints": [], "columns": [{"name": "값", "type": "timestamp with time zone", "nullable": True}]},
    {"name": "class", "comment": "line1\nline2\u000b", "constraints": [{"kind": "p", "columns": ["pk"]}],
     "columns": [{"name": "pk", "type": "bigint", "nullable": False}, {"name": "FK", "type": "bigint", "nullable": True}]},
]


@unittest.skipUnless(NODE, "node 필요")
class MermaidTest(unittest.TestCase):
    def test_keys_relationships_and_awkward_names(self):
        out = mermaid_of(AWKWARD)
        self.assertTrue(out.startswith("%% t\nerDiagram\n"))
        self.assertIn("    varchar(255) email UK \"NOT NULL\"", out)
        self.assertIn("    numeric total \"numeric(14,2) · NOT NULL · say 'hi'\"", out)
        self.assertIn('  members |o..o{ orders : "member_id"', out)
        self.assertIn('  orders ||..o| receipts : "order_id"', out)
        self.assertNotIn("coupons", out)
        self.assertIn('  e_주문_상세["주문 상세"] {', out)
        self.assertIn("    timestamptz 값", out)
        self.assertIn('  e_class["class"] {', out)
        self.assertIn('    bigint pk_ PK "이름 pk"', out)
        self.assertIn('    bigint FK_ "이름 FK"', out)

    def test_legacy_compat_uses_ascii_names(self):
        code = ("const {mermaidER}=require(process.argv[1]);let s='';process.stdin.on('data',c=>s+=c);"
                "process.stdin.on('end',()=>process.stdout.write(mermaidER(JSON.parse(s),{compat:'legacy'})))")
        out = subprocess.run([NODE, "-e", code, str(ROOT / "templates" / "export.js")], input=json.dumps(AWKWARD),
                             capture_output=True, text=True, check=True).stdout
        self.assertNotIn('["', out)
        self.assertIn('  %% e______: 주문 상세\n  e______ {', out)
        self.assertIn('  %% e_class: class\n  e_class {', out)
        for line in out.splitlines():
            if line.endswith("{"):
                self.assertRegex(line, r"^  [A-Za-z_][A-Za-z0-9_]* \{$")

    def test_alias_escapes_and_key_named_types(self):
        out = mermaid_of([
            {"name": "a\\b", "columns": [{"name": "x", "type": "PK", "nullable": True}]},
            {"name": "a%%b", "columns": [{"name": "y", "type": "uk", "nullable": True}]},
        ])
        self.assertIn('  e_a_b["a\uff3cb"] {', out)
        self.assertIn('  e_a__b["a\uff05\uff05b"] {', out)
        self.assertIn('    PK_ x "PK"', out)
        self.assertIn('    uk_ y "uk"', out)

    def test_cli_rejects_missing_path(self):
        r = subprocess.run([NODE, str(ROOT / "scripts" / "export_erd.mjs"), "/nonexistent/x.json"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("없는 경로", r.stderr)

    def test_cli_reads_timeline_output(self):
        base = ROOT / "out" / "timeline"
        if not (base / "steps.json").exists():
            self.skipTest("out/timeline 재생 결과 없음")
        out = subprocess.run([NODE, str(ROOT / "scripts" / "export_erd.mjs"), str(base), "--step", "1", "--tables", "orders,members"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("erDiagram", out)
        self.assertIn("members ||..o{ orders", out)


def timeline_from(before, after, d):
    """스냅샷 두 개로 1단계 타임라인(DDL 두 개 비교와 같은 경로)을 만들고 timeline.html 경로를 돌려준다."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from compare_timeline import build
    out = Path(d) / "t"
    build(before, after, out)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "timeline.py"), str(out)], check=True, capture_output=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "render_timeline.py"), str(out), "--out", str(out / "timeline.html")],
                   check=True, capture_output=True)
    return out / "timeline.html"


def fixture(*parts):
    return json.loads((ROOT / "tests" / "fixtures" / Path(*parts)).read_text())


@unittest.skipUnless(NODE and chrome(), "node 와 Chrome 필요")
class CheckHtmlTest(unittest.TestCase):
    def run_check(self, html, *extra):
        r = subprocess.run([NODE, str(ROOT / "scripts" / "check_html.mjs"), str(html), *extra], capture_output=True, text=True, timeout=300)
        return r.returncode, json.loads(r.stdout)

    def test_rendered_compare_passes(self):
        with tempfile.TemporaryDirectory() as d:
            out = timeline_from(fixture("injection", "00.json"), fixture("injection", "01.json"), d)
            code, receipt = self.run_check(out)
            self.assertEqual(code, 0, [c for c in receipt["checks"] if c["ok"] is False])
            self.assertTrue(Path(receipt["screenshot"]).exists())
            names = {c["name"] for c in receipt["checks"] if c["ok"]}
            self.assertIn("변경 블록이 변경 수만큼 그려짐 (마지막 단계)", names)
            self.assertIn("관계도에 보일 테이블이 모두 그려짐 (마지막 단계)", names)
            html = out.read_text()
            self.assertIn('"kind": "compare"', html)

    def test_awkward_schema_page_exports_parse(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_timeline import snap
        before = snap([(1, "class", [(1, "pk", "bigint"), (2, "fk", "bigint")], []), (2, "style", [(1, "id", "int")], [])], rid="a")
        after = snap([(1, "class", [(1, "pk", "bigint"), (2, "fk", "bigint"), (3, "이름", "text")], []), (3, "주문 상세", [(1, "값", "int")], [])], rid="b")
        with tempfile.TemporaryDirectory() as d:
            out = timeline_from(before, after, d)
            code, receipt = self.run_check(out)
            self.assertEqual(code, 0, [c for c in receipt["checks"] if c["ok"] is False])
            names = {c["name"]: c for c in receipt["checks"]}
            if names.get("Mermaid ERD 가 파싱됨", {}).get("ok") is None:
                self.skipTest("로컬 mermaid 가 없어 파싱은 확인하지 못함")

    def test_broken_page_fails(self):
        with tempfile.TemporaryDirectory() as d:
            out = timeline_from(fixture("00.json"), fixture("01.json"), d)
            out.write_text(out.read_text().replace("</body>", "<div style='width:4000px;height:1px'></div><script>throw new Error('x')</script></body>"))
            code, receipt = self.run_check(out)
            self.assertEqual(code, 1)
            failed = {c["name"] for c in receipt["checks"] if c["ok"] is False}
            self.assertIn("1280px 에서 가로 넘침 없음", failed)
            self.assertIn("스크립트·콘솔 오류 없음", failed)

    def test_hanging_page_fails_not_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "hang.html"
            out.write_text("<!doctype html><title>t</title><div id=blocks></div><script>setTimeout(() => { while (true) {} }, 300)</script>")
            code, receipt = self.run_check(out, "--timeout", "20")
            self.assertEqual(code, 1, receipt)
            self.assertIs(receipt["ok"], False)
            self.assertIn("제한 시간 안에 점검을 마침", {c["name"] for c in receipt["checks"] if c["ok"] is False})


def has_docker():
    return shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@unittest.skipUnless(has_docker() and NODE and chrome(), "docker·node·Chrome 필요")
class CompareScriptTest(unittest.TestCase):
    def test_compare_sh_makes_one_step_timeline(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(["bash", str(ROOT / "scripts" / "compare.sh"), str(ROOT / "samples" / "v1.sql"),
                                str(ROOT / "samples" / "v2.sql"), d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(Path(d, "timeline.html").exists())
            self.assertFalse(Path(d, "compare.html").exists())
            meta = json.loads(Path(d, "steps.json").read_text())
            self.assertEqual((meta["kind"], meta["base_source"], len(meta["steps"])), ("compare", "v1.sql", 1))
            self.assertTrue(json.loads(Path(d, "check.json").read_text())["ok"])


if __name__ == "__main__":
    unittest.main()
