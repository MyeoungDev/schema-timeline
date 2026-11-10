"""templates/diff.js(브라우저 구간 비교)가 scripts/diff.py 와 같은 결과를 내는지 확인한다. node 가 없으면 건너뛴다."""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from diff import diff  # noqa: E402
from test_timeline import fk, snap  # noqa: E402


def js_diff(pairs):
    out = subprocess.run(["node", str(ROOT / "tests" / "diff_parity.js")], input=json.dumps(pairs),
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def canon(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


def assert_parity(case, pairs):
    got = js_diff(pairs)
    for k, ((b, a), j) in enumerate(zip(pairs, got)):
        py = diff(b, a)
        if canon(py) != canon(j):
            for field in ("tables", "views", "events", "counts"):
                if canon(py[field]) != canon(j[field]):
                    case.fail(f"쌍 {k}: {field} 가 다릅니다\npython={canon(py[field])[:600]}\njs    ={canon(j[field])[:600]}")


@unittest.skipUnless(shutil.which("node"), "node 필요")
class DiffParityTest(unittest.TestCase):
    def test_fixtures(self):
        pairs = []
        for d in [ROOT / "tests" / "fixtures"] + sorted(p for p in (ROOT / "tests" / "fixtures").iterdir() if p.is_dir()):
            if (d / "00.json").exists():
                pairs.append([json.loads((d / "00.json").read_text()), json.loads((d / "01.json").read_text())])
        self.assertGreaterEqual(len(pairs), 5)
        assert_parity(self, pairs)

    def test_synthetic_cases(self):
        b1 = snap([(1, "m", [(1, "id", "int"), (2, "code", "text"), (3, "x", "text")], [])])
        a1 = snap([(1, "m", [(1, "id", "int"), (3, "code", "text")], [])])
        b2 = snap([(1, "orders", [(1, "id", "int")], []), (2, "orders_new", [(1, "id", "int")], [])])
        a2 = snap([(2, "orders", [(1, "id", "int")], [])])
        b3 = snap([(1, "members", [(1, "id", "int")], []), (2, "child", [(1, "m", "int")], [fk("child_m_fkey", "m", "members", "id")])])
        a3 = snap([(1, "members", [(1, "member_id", "int")], []), (2, "child", [(1, "m", "int")], [fk("child_m_fkey", "m", "members", "member_id")])])
        b4 = snap([(1, "t", [(1, "a", "int"), (2, "b", "text")], [])], rid="x")
        a4 = snap([(9, "t", [(1, "a", "bigint"), (2, "c", "text")], [])], rid="y")
        recreated = snap([(7, "t", [(1, "a", "int")], [])], rid="r")
        recreated2 = snap([(8, "t", [(1, "a", "int"), (2, "z", "int")], [])], rid="r")
        assert_parity(self, [[b1, a1], [b2, a2], [b3, a3], [b4, a4], [a1, b1], [recreated, recreated2]])

    def test_identifier_edges_and_sorting(self):
        b = snap([(1, "t", [(1, "amount$", "int")], [{"name": "c", "kind": "c", "columns": ["amount$"], "definition": "CHECK ((amount$ > 0))",
                                                      "ref_table": None, "ref_columns": None}]),
                  (2, "ｚ", [(1, "id", "int")], []), (3, "😀", [(1, "id", "int")], []), (4, "이름", [(1, "값", "text")], [])])
        a = snap([(1, "t", [(1, "total$", "int")], [{"name": "c", "kind": "c", "columns": ["total$"], "definition": "CHECK ((total$ > 0))",
                                                     "ref_table": None, "ref_columns": None}]),
                  (2, "ｚ", [(1, "id", "int")], []), (3, "😀", [(1, "id", "int")], []), (4, "이름", [(1, "값2", "text")], [])])
        b["views"] = [{"name": "😀v", "definition": "x"}, {"name": "ｚv", "definition": "y"}]
        a["views"] = [{"name": "ｚv", "definition": "y2"}]
        assert_parity(self, [[b, a]])

    def test_linked_full_snapshots(self):
        from timeline import link_by_name
        s0 = {"tables": [], "views": []}
        s1 = snap([(500, "users", [(1, "id", "int"), (2, "nm", "text")], [])], rid="r1")
        s2 = snap([(600, "members", [(1, "id", "int"), (2, "nm", "text")], [])], rid="r2")
        s3 = snap([(700, "members", [(1, "id", "int"), (2, "nm", "text"), (3, "x", "int")], [])], rid="r3")
        s4 = snap([(800, "members", [(1, "id", "int"), (2, "name", "text"), (3, "x", "int")], [])], rid="r4")
        s5 = snap([(900, "members", [(1, "id", "int"), (2, "name", "varchar(10)"), (3, "x", "int")], [])], rid="r5")
        snaps = link_by_name([s0, s1, s2, s3, s4, s5])
        for s in snaps:
            s["linked"] = True
        pairs = [[snaps[i], snaps[j]] for i in range(len(snaps)) for j in range(i + 1, len(snaps))]
        assert_parity(self, pairs)
        d = diff(snaps[1], snaps[3])
        self.assertEqual([(t["name"], t["status"], t.get("confidence")) for t in d["tables"]], [("members", "renamed", "suspected")])
        d = diff(snaps[3], snaps[5])
        self.assertEqual([e["kind"] for e in d["events"]], ["column_renamed", "column_type"])
        self.assertEqual(d["events"][0]["confidence"], "suspected")

    def test_step_diffs_match_view_with_ids(self):
        for view_file in sorted((ROOT / "out").glob("*/snapshots_view.json")):
            base, name = view_file.parent, view_file.parent.name
            view = json.loads((base / "snapshots_view.json").read_text())
            steps = len(view) - 1
            got = js_diff([[view[i - 1], view[i], True] for i in range(1, steps + 1)])
            for i, j in enumerate(got, 1):
                py = json.loads((base / "diffs" / f"{i:03d}.json").read_text())
                strip = lambda evs: [{k: v for k, v in e.items() if k not in ("oid", "attnum")} for e in evs]
                for field in ("tables", "views", "counts"):
                    self.assertEqual(canon(py[field]), canon(j[field]), f"{name} 단계 {i} {field}")
                self.assertEqual(canon(py["events"]), canon(strip(j["events"])), f"{name} 단계 {i} events")

    def test_replayed_history_all_pairs(self):
        snaps_dir = ROOT / "out" / "timeline" / "snapshots"
        if not snaps_dir.exists():
            self.skipTest("out/timeline 재생 결과 없음")
        snaps = [json.loads(p.read_text()) for p in sorted(snaps_dir.glob("*.json"))]
        pairs = [[snaps[i], snaps[j]] for i in range(len(snaps)) for j in range(i + 1, len(snaps))]
        assert_parity(self, pairs)


if __name__ == "__main__":
    unittest.main()
