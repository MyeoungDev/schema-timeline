"""samples/v1.sql → v2.sql 스냅샷의 diff 결과가 샘플에 넣은 변경과 정확히 일치하는지 확인한다.

fixtures 는 scripts/snapshot.py 로 만든 스냅샷이다. 실행: python3 -m unittest discover tests
"""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from diff import diff  # noqa: E402

EXPECTED = {
    ("table_added", "products", None),
    ("table_dropped", "legacy_coupons", None),
    ("column_type", "users", "email"),
    ("column_nullable", "users", "name"),
    ("column_added", "users", "phone"),
    ("column_renamed", "orders", "status"),
    ("column_comment", "orders", "status"),
    ("column_type", "orders", "total"),
    ("column_added", "orders", "shipped_at"),
    ("column_dropped", "orders", "memo"),
    ("index_added", "orders", None),
    ("column_added", "order_items", "product_id"),
    ("column_dropped", "order_items", "product_name"),
    ("column_default", "order_items", "qty"),
    ("constraint_added", "order_items", None),
    ("view_added", "order_summary", None),
}


class SampleDiffTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fx = ROOT / "tests" / "fixtures"
        cls.result = diff(json.loads((fx / "00.json").read_text()), json.loads((fx / "01.json").read_text()))

    def test_events_match_sample_changes(self):
        got = {(e["kind"], e["table"], e["column"]) for e in self.result["events"]}
        self.assertEqual(got, EXPECTED)
        self.assertEqual(self.result["counts"]["constraint_added"], 2)

    def test_rename_is_only_suspected(self):
        renames = [e for e in self.result["events"] if e["kind"] == "column_renamed"]
        self.assertEqual([(e["before"], e["after"], e["confidence"]) for e in renames],
                         [("state", "status", "suspected")])

    def test_different_type_is_not_paired_as_rename(self):
        items = next(t for t in self.result["tables"] if t["name"] == "order_items")
        status = {c["name"]: c["status"] for c in items["columns"]}
        self.assertEqual(status["product_name"], "dropped")
        self.assertEqual(status["product_id"], "added")

    def test_dropped_column_keeps_its_position(self):
        orders = next(t for t in self.result["tables"] if t["name"] == "orders")
        self.assertEqual([c["name"] for c in orders["columns"]],
                         ["id", "user_id", "status", "total", "memo", "created_at", "shipped_at"])

    def test_dropped_tables_come_last(self):
        self.assertEqual([t["name"] for t in self.result["tables"]],
                         ["products", "users", "orders", "order_items", "legacy_coupons"])


if __name__ == "__main__":
    unittest.main()
