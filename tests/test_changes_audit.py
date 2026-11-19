"""企业变化影响、金额拆分与回查审计的测试。"""

import unittest
from pathlib import Path

from procurement.audit import DecisionAuditor
from procurement.changes import ChangeAnalyzer
from procurement.portfolio import PortfolioBuilder
from procurement.snapshot import load_snapshot

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "supplies.json"


class ChangeAndAuditTest(unittest.TestCase):
    def test_no_change_at_default_decision_point(self):
        snap = load_snapshot(DATA)
        self.assertEqual(ChangeAnalyzer(snap).impacts(), [])

    def test_later_change_lists_affected_commitment_and_money_split(self):
        snap = load_snapshot(DATA, decision_at="2026-12-03")
        impacts = ChangeAnalyzer(snap).impacts()
        self.assertEqual(len(impacts), 1)
        impact = impacts[0]
        self.assertEqual(impact.group, "N01")
        affected_people = {item.person for item in impact.affected}
        self.assertIn("P001", affected_people)
        # T05 价格 1900，其中不可退 600、可收回 1300。
        self.assertEqual(impact.sunk_cost, 600)
        self.assertEqual(impact.recoverable_cost, 1300)
        # 原接收承诺仍可查，没有被无声挪走。
        self.assertTrue(
            any(a.person == "P001" for a in snap.acceptances)
        )

    def test_original_posting_remains_visible_after_change(self):
        snap = load_snapshot(DATA, decision_at="2026-12-03")
        state = PortfolioBuilder(snap).groups.slot_state("N01")
        self.assertEqual(state.original_start, "2027-02-12")
        self.assertEqual(state.start, "2027-01-12")
        self.assertTrue(state.start_moved)
        self.assertTrue(state.reduced)

    def test_audit_separates_solid_conditional_and_projected(self):
        snap = load_snapshot(DATA)
        builder = PortfolioBuilder(snap)
        plan = builder.comparison_plans()[1]
        entries = DecisionAuditor(snap).audit(plan)
        self.assertEqual(len(entries), len(builder.groups.groups))
        for entry in entries:
            self.assertGreaterEqual(entry.solid_positions, 0)
            self.assertGreaterEqual(entry.conditional_positions, 0)
            self.assertGreaterEqual(entry.projected_high, entry.projected_low)
            self.assertTrue(entry.trace)
            # 证据不足时审计依据必须明确声明无因果担保。
            self.assertIn("无因果担保", entry.evidence_basis)

    def test_audit_is_traceable_to_qualification_and_arm_strength(self):
        snap = load_snapshot(DATA)
        entries = {
            e.group: e
            for e in DecisionAuditor(snap).audit(
                PortfolioBuilder(snap).comparison_plans()[1]
            )
        }
        n01 = entries["N01"]
        self.assertIn("R1", n01.qualification_basis)
        self.assertIn("高邀约", n01.evidence_basis)
        self.assertIn("低邀约", n01.evidence_basis)
        # 待确认证据组要标明条件性。
        n00 = entries["N00"]
        self.assertIn("待确认", n00.qualification_basis)


if __name__ == "__main__":
    unittest.main()
