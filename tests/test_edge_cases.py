"""tests/cases 의 DDL 쌍으로 만든 스냅샷(tests/fixtures/<case>)에 대한 diff·render 경계 사례 검증.

fixtures 갱신: python3 scripts/snapshot.py tests/cases/<case>_before.sql tests/cases/<case>_after.sql --out tests/fixtures/<case>
"""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from diff import diff  # noqa: E402


def run_case(name):
    fx = ROOT / "tests" / "fixtures" / name
    return diff(json.loads((fx / "00.json").read_text()), json.loads((fx / "01.json").read_text()))


def kinds(result):
    return sorted((e["kind"], e["table"], e["column"]) for e in result["events"])


class EdgeCaseTest(unittest.TestCase):
    def test_fk_option_and_partial_index_changes_are_detected(self):
        self.assertEqual(kinds(run_case("fk_index")), [
            ("constraint_added", "c", None), ("constraint_dropped", "c", None),
            ("index_added", "u", None), ("index_dropped", "u", None),
        ])

    def test_ambiguous_rename_is_not_paired(self):
        self.assertEqual(kinds(run_case("ambiguous")), [
            ("column_added", "t", "p"), ("column_dropped", "t", "x"), ("column_dropped", "t", "y"),
        ])

    def test_table_rename_has_no_side_effect_events(self):
        result = run_case("table_rename")
        self.assertEqual(kinds(result), [("table_renamed", "accounts", None)])
        self.assertEqual(result["events"][0]["confidence"], "suspected")

    def test_mixed_case_fk_target_uses_bare_table_name(self):
        result = run_case("injection")
        notes = next(t for t in result["tables"] if t["name"] == "notes")
        fk = next(k for k in notes["constraints"] if k["kind"] == "f")
        self.assertEqual(fk["ref_table"], "Users")

    def test_render_escapes_script_breaking_text(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from render import embed, template
        html = embed(template("compare.html"), run_case("injection"))
        data = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
        self.assertNotIn("<", data)
        self.assertIn("onerror", json.loads(data)["events"][0]["after"])


if __name__ == "__main__":
    unittest.main()
