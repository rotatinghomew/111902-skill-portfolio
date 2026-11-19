"""快照与决策时点规则的测试。"""

import unittest
from pathlib import Path

from procurement.snapshot import load_snapshot

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "supplies.json"


class SnapshotTest(unittest.TestCase):
    def test_default_decision_point_excludes_later_records(self):
        snap = load_snapshot(DATA)
        self.assertEqual(snap.decision_at.isoformat(), "2026-12-01")
        # 2026-12-02 送达的企业变化与新资格证据在默认时点不可见。
        self.assertEqual(snap.changes, [])
        self.assertFalse(
            any(q.evidence == "RX" for q in snap.qualifications)
        )

    def test_later_decision_point_sees_change_and_replacement_evidence(self):
        snap = load_snapshot(DATA, decision_at="2026-12-03")
        self.assertEqual(len(snap.changes), 1)
        self.assertTrue(
            any(q.evidence == "RX" for q in snap.qualifications)
        )

    def test_maturity_separates_rounds(self):
        snap = load_snapshot(DATA)
        evidence_rounds = {r.round for r in snap.rounds}
        mature = [r for r in evidence_rounds if snap.is_round_mature(r)]
        # 数据中 54 个批次观察期已满，106 个未满，未满者不得进入结局比较。
        self.assertEqual(len(mature), 54)
        self.assertEqual(len(evidence_rounds) - len(mature), 106)

    def test_immutable_snapshot_not_mutated_by_rerun(self):
        first = load_snapshot(DATA)
        second = load_snapshot(DATA, decision_at="2026-12-03")
        self.assertEqual(first.changes, [])
        self.assertEqual(len(second.changes), 1)


if __name__ == "__main__":
    unittest.main()
