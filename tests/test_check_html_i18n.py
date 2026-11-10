"""check_html.mjs 의 영어 화면 한글 잔존 검사: 데이터에 있는 한글은 통과, UI 에 남은 한글은 실패.

node 와 Chrome 이 없으면 건너뛴다.
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
sys.path.insert(0, str(Path(__file__).parent))
import i18n  # noqa: E402
from compare_timeline import build  # noqa: E402
from test_export_check import chrome  # noqa: E402
from test_i18n import snap, table  # noqa: E402

NODE = shutil.which("node")
ID = "english-no-hangul"


def english_page(out, comment):
    """표·열 주석에 comment 를 넣은 영어 비교 화면(timeline.html)을 만든다."""
    a, b = table("a", ["x"]), table("a", ["x", "y"])
    for t in (a, b):
        t["comment"] = comment
    b["columns"][1]["comment"] = comment
    i18n.set_lang("en")
    try:
        build(snap(None, [a]), snap(None, [b]), out)
    finally:
        i18n.set_lang(None)
    env = {**os.environ, "SCHEMA_TIMELINE_LANG": "en"}
    for s, args in (("timeline.py", [str(out), "--lang", "en"]),
                    ("render_timeline.py", [str(out), "--out", str(out / "timeline.html"), "--lang", "en"])):
        subprocess.run([sys.executable, str(ROOT / "scripts" / s), *args], check=True, capture_output=True, env=env)
    return out / "timeline.html"


@unittest.skipUnless(NODE and chrome(), "node 와 Chrome 필요")
class HangulLeakTest(unittest.TestCase):
    def check(self, html):
        r = subprocess.run([NODE, str(ROOT / "scripts" / "check_html.mjs"), str(html)], capture_output=True, text=True, timeout=300)
        receipt = json.loads(r.stdout)
        return receipt, {c["id"]: c for c in receipt["checks"]}

    def page(self, comment="삭제 정책"):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        return english_page(d / "o", comment)

    def test_hangul_only_in_data_passes_and_receipt_is_english(self):
        receipt, checks = self.check(self.page())
        self.assertTrue(receipt["ok"], [c for c in receipt["checks"] if c["ok"] is False])
        self.assertIs(checks[ID]["ok"], True)
        self.assertEqual(checks[ID]["name"], "No Korean text left on the English page")
        self.assertTrue(all(c["id"] for c in receipt["checks"]))

    def test_korean_in_review_template_fails_even_if_word_is_in_data(self):
        html = self.page()
        text = html.read_text()
        self.assertIn("Click a table to jump to its changes below", text)
        html.write_text(text.replace("Click a table to jump to its changes below", "삭제"))
        receipt, checks = self.check(html)
        self.assertFalse(receipt["ok"])
        self.assertIs(checks[ID]["ok"], False)
        self.assertIn("삭제", checks[ID]["detail"])

    def test_korean_in_top_page_ui_fails(self):
        html = self.page()
        text = html.read_text()
        self.assertIn("Full DDL at this version", text)
        html.write_text(text.replace("Full DDL at this version", "삭제"))
        _, checks = self.check(html)
        self.assertIs(checks[ID]["ok"], False)


if __name__ == "__main__":
    unittest.main()
