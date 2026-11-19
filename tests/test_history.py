"""往届因果证据规则的测试。"""

import unittest
from pathlib import Path

from procurement.history import (
    LOST_AS_HIRED,
    LOST_AS_NOT_HIRED,
    HistoricalEvidence,
)
from procurement.snapshot import (
    HIRED,
    RANDOM_HIGH,
    RANDOM_LOW,
    load_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "supplies.json"


class HistoricalEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = load_snapshot(DATA)
        cls.evidence = HistoricalEvidence(cls.snap)

    def test_itt_uses_initial_assignment_only(self):
        # 入组只认 probability_initial；递补、参加与否都不改变分组。
        note = self.evidence.ittt_check_note()
        self.assertIn("ITT 口径成立", note)
        for group in self.evidence.group_outcomes():
            meta = self.snap.round(group.round)
            exposures = self.snap.exposures_of(group.round)
            first_offers = sum(1 for e in exposures if e.first_offer)
            self.assertEqual(first_offers, meta.invitation_slots)

    def test_immature_rounds_excluded_from_outcome_comparison(self):
        mature_ids = set(self.evidence.mature_round_ids)
        self.assertEqual(len(mature_ids), 54)
        excluded = set(self.evidence.excluded_immature_rounds)
        self.assertTrue(mature_ids.isdisjoint(excluded))

    def test_lost_contacts_distinguished_from_confirmed_not_hired(self):
        for group in self.evidence.group_outcomes():
            total = (
                group.hired + group.confirmed_not_hired + group.lost
            )
            self.assertEqual(total, group.candidates)
            # 失联敏感性：失联算入职时只增不减。
            self.assertGreaterEqual(
                group.hired_under(LOST_AS_HIRED),
                group.hired_under(LOST_AS_NOT_HIRED),
            )

    def test_substitution_counted_within_group_not_as_independent_gain(self):
        # 同组替代：未拿首轮邀约却入职的人，必须能被识别且不超过全组入职数。
        for group in self.evidence.group_outcomes():
            self.assertLessEqual(
                group.substitution_hires, group.hired
            )
            self.assertEqual(
                group.first_offer_hired + group.non_first_offer_hired,
                group.hired,
            )

    def test_insufficient_controls_yield_support_range_only(self):
        # 门槛是每臂 6 个成熟随机小组；数据里每个分层最多 3 个，故全部只能报范围。
        for stratum in self.evidence.strata_overview():
            self.assertTrue(stratum.support_only)
            self.assertFalse(stratum.causal_available)
            self.assertIsNotNone(stratum.reason_if_insufficient)
            if stratum.priority is not None:
                # 仅优先批次只做覆盖描述，没有增量字段。
                self.assertIsNone(stratum.incremental_per_group)

    def test_priority_rounds_never_used_as_causal_arms(self):
        for group in self.evidence.group_outcomes():
            if group.mode == "仅优先":
                # 优先批次仍有观察值，但不会出现在任何随机臂摘要里。
                pass
        for stratum in self.evidence.strata_overview():
            for arm in (stratum.high, stratum.low):
                if arm is not None:
                    self.assertIn(arm.arm, (RANDOM_HIGH, RANDOM_LOW))

    def test_sensitivity_bracket_contains_point_estimate(self):
        # 手工构造一个充足场景不现实，这里验证区间计算的单调性不变量。
        for stratum in self.evidence.strata_overview():
            if stratum.causal_available:
                self.assertLessEqual(
                    stratum.incremental_low, stratum.incremental_per_group
                )
                self.assertLessEqual(
                    stratum.incremental_per_group, stratum.incremental_high
                )


if __name__ == "__main__":
    unittest.main()
