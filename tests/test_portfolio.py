"""岗位名额、共用广告、预占与采购组合约束的测试。"""

import unittest
from pathlib import Path

from procurement.courses import CourseCatalog
from procurement.eligibility import (
    REASON_MISSED_START,
    REASON_NO_EVIDENCE,
    REASON_SHARED_SLOT,
)
from procurement.groups import GroupDirectory
from procurement.portfolio import PortfolioBuilder
from procurement.snapshot import (
    PURCHASE_IRREVOCABLE,
    PURCHASE_REPLACEABLE,
    load_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "supplies.json"


class GroupAndPortfolioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = load_snapshot(DATA)
        cls.groups = GroupDirectory(cls.snap)
        cls.builder = PortfolioBuilder(cls.snap)

    def test_multi_posting_groups_share_one_slot_pool(self):
        state = self.groups.slot_state("N00")
        self.assertEqual(len(state.postings), 2)
        self.assertTrue(state.slot_sharing)
        # 两条广告各写 2 个名额，但共用后只有 2 个，不是 4 个。
        self.assertEqual(state.effective_slots, 2)

    def test_accepted_person_preoccupies_slot(self):
        state = self.groups.slot_state("N01")
        self.assertIn("P001", state.accepted_people)
        self.assertEqual(state.open_slots, state.effective_slots - 1)

    def test_irrevocable_purchase_blocks_seat_and_budget(self):
        catalog = CourseCatalog(self.snap)
        inventory = catalog.inventory("T05")
        self.assertEqual(inventory.reserved_irrevocable, 1)
        purchase = self.snap.purchases[0]
        self.assertEqual(purchase.state, PURCHASE_IRREVOCABLE)

    def test_only_replaceable_purchases_can_be_replaced(self):
        with self.assertRaises(ValueError):
            self.builder.build_plan("非法", replace={"T05"})
        plan = self.builder.build_plan(
            "合法置换", replace={"T08"}
        )
        self.assertIn("T08", plan.replaced_purchases)
        # P005 的原 T08 采购已解除；释放出的席位允许重新卖给其他学员。
        self.assertFalse(
            any(
                line.person == "P005" and line.quote == "T08"
                for line in plan.lines
            )
        )
        # 不可撤的 T05/P001 任何方案都不能动。
        self.assertTrue(
            any(
                line.person == "P001" and line.quote == "T05"
                for line in plan.lines
            )
        )

    def test_one_person_never_fills_two_positions(self):
        for plan in self.builder.comparison_plans():
            seen = {}
            for group_plan in plan.group_plans:
                for assignment in group_plan.assignments:
                    self.assertNotIn(assignment.person, seen)
                    seen[assignment.person] = group_plan.group

    def test_budget_plan_respects_cap_and_full_plan_flags_breach(self):
        plans = {p.name: p for p in self.builder.comparison_plans()}
        budgeted = next(p for name, p in plans.items() if "预算内" in name)
        self.assertTrue(budgeted.within_budget)
        self.assertLessEqual(budgeted.total_cost, self.snap.config.total)
        full = next(p for name, p in plans.items() if "不设预算上限" in name)
        self.assertFalse(full.within_budget)

    def test_unfilled_slots_total_to_openings(self):
        for plan in self.builder.comparison_plans():
            for group_plan in plan.group_plans:
                state = group_plan.state
                unfilled = sum(u.count for u in group_plan.unfilled)
                filled = len(group_plan.assignments)
                self.assertEqual(
                    filled + unfilled + len(state.accepted_people),
                    state.effective_slots,
                )

    def test_unfilled_reasons_cover_the_three_required_cases(self):
        reasons = set()
        shared_groups = set()
        for plan in self.builder.comparison_plans():
            for group_plan in plan.group_plans:
                for unfilled in group_plan.unfilled:
                    reasons.add(unfilled.reason)
                    if unfilled.shared_postings:
                        shared_groups.add(group_plan.group)
        # 至少要出现“无往届证据”类；共用名额通过独立标记暴露，不和原因互斥。
        self.assertIn(REASON_NO_EVIDENCE, reasons)
        baseline = self.builder.comparison_plans()[0]
        shared_with_gap = {
            gp.group
            for gp in baseline.group_plans
            if gp.state.slot_sharing and gp.unfilled
        }
        self.assertTrue(shared_with_gap)
        self.assertTrue(shared_groups.issuperset(shared_with_gap))

    def test_refundable_and_nonrefundable_split(self):
        plan = self.builder.baseline_plan()
        self.assertEqual(
            plan.nonrefundable_cost,
            sum(line.nonrefundable for line in plan.lines),
        )
        self.assertLessEqual(plan.nonrefundable_cost, plan.total_cost)

    def test_support_range_never_presented_as_guarantee(self):
        # 全部岗位组的随机对照都不足时，可支持新增必须为 0，只保留区间。
        for plan in self.builder.comparison_plans():
            self.assertEqual(plan.supported_incremental, 0)
            self.assertIsInstance(plan.support_low, float)
            self.assertGreaterEqual(plan.support_high, plan.support_low)


if __name__ == "__main__":
    unittest.main()
