"""用受控最小快照验证时程分支，不依赖合成数据的碰巧覆盖。"""

import unittest
from datetime import timedelta

from procurement.eligibility import (
    IN_FLIGHT,
    NO_EMPLOYER_EVIDENCE,
    SATISFIED,
    EligibilityEngine,
)
from procurement.portfolio import (
    REASON_MISSED_START,
    PortfolioBuilder,
)
from procurement.snapshot import (
    Candidate,
    Certificate,
    Posting,
    ProcurementConfig,
    Qualification,
    Quote,
    Snapshot,
)
from procurement.timeutil import parse_date


def _minimal_snapshot(quote_usable_from: str, start: str = "2027-01-12"):
    source = {
        "synthetic": True,
        "tables": {
            "historical_rounds": [],
            "exposures": [],
            "outcomes": [],
            "postings": [
                {
                    "posting": "M-A",
                    "opening_group": "M0",
                    "region": "城区",
                    "trade": "运维",
                    "slots": 1,
                    "start": start,
                    "received": "2026-11-10",
                }
            ],
            "qualifications": [
                {
                    "evidence": "RM",
                    "group": "M0",
                    "alternatives": [["甲类"]],
                    "status": "企业确认",
                    "received": "2026-11-12",
                    "revokes": None,
                }
            ],
            "changes": [],
            "candidates": [
                {
                    "person": "M1",
                    "available": ["周四下午"],
                    "groups": ["M0"],
                    "region": "城区",
                }
            ],
            "certificates": [
                {
                    "person": "M1",
                    "skill": "安全底训",
                    "evidence": "已核",
                    "expires": "2027-12-31",
                }
            ],
            "quotes": [
                {
                    "quote": "QM",
                    "provides": "甲类",
                    "seats": 5,
                    "requires": [],
                    "session": "2026-12-10",
                    "exam": "2026-12-20",
                    "usable_from": quote_usable_from,
                    "slot": "周四下午",
                    "teacher": "教员M",
                    "exam_room": "考点M",
                    "cost": 1000,
                    "nonrefundable": 400,
                    "expires": "2026-12-01",
                }
            ],
            "purchases": [],
            "accepted": [],
            "procurement": [
                {
                    "total": 5000,
                    "decision_at": "2026-12-01",
                    "min_random_groups_per_arm": 6,
                    "confidence": 0.9,
                }
            ],
        },
    }
    return Snapshot(source)


class TimingBranchTest(unittest.TestCase):
    def test_course_ready_before_start_is_inflight(self):
        snap = _minimal_snapshot("2027-01-05")
        engine = EligibilityEngine(snap)
        row = engine.evaluate_candidate("M1", "M0")
        self.assertEqual(row.state, IN_FLIGHT)
        self.assertEqual(row.best_path.quotes[0].quote, "QM")

    def test_course_ready_after_start_is_blocked(self):
        snap = _minimal_snapshot("2027-01-20")
        engine = EligibilityEngine(snap)
        row = engine.evaluate_candidate("M1", "M0")
        self.assertEqual(row.state, NO_EMPLOYER_EVIDENCE)
        self.assertTrue(
            any("开工" in blocker for blocker in row.blockers)
        )

    def test_portfolio_marks_missed_start_when_only_late_quotes_exist(self):
        # 让早课程不存在：直接把唯一课程改成晚于开工，组合层应归入赶不上开工类。
        snap = _minimal_snapshot("2027-01-20")
        builder = PortfolioBuilder(snap)
        plan = builder.build_plan("时程测试")
        group_plan = next(gp for gp in plan.group_plans if gp.group == "M0")
        self.assertEqual(len(group_plan.assignments), 0)
        self.assertTrue(group_plan.unfilled)
        reasons = {u.reason for u in group_plan.unfilled}
        self.assertIn(REASON_MISSED_START, reasons)


if __name__ == "__main__":
    unittest.main()
