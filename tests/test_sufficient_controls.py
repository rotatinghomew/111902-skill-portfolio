"""对照充足时必须给出 ITT 因果增量，而不是只报支持范围。"""

import copy
import json
import unittest
from pathlib import Path

from procurement.history import HistoricalEvidence
from procurement.portfolio import PortfolioBuilder
from procurement.snapshot import Snapshot

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "supplies.json"


def _source_with_sufficient_controls() -> dict:
    source = json.loads(DATA.read_text(encoding="utf-8"))
    base_snapshot = Snapshot(source)
    tables = source["tables"]
    ex_by_round: dict[str, list] = {}
    oc_by_round: dict[str, list] = {}
    for row in tables["exposures"]:
        ex_by_round.setdefault(row["round"], []).append(row)
    for row in tables["outcomes"]:
        oc_by_round.setdefault(row["round"], []).append(row)

    mature_random = [
        meta.round
        for meta in base_snapshot.rounds
        if base_snapshot.is_round_mature(meta.round)
        and meta.is_random
        and meta.trade == "运维"
        and meta.locality == "城区"
    ]
    new_rounds, new_exposures, new_outcomes = [], [], []
    clone_id = 0
    for round_id in mature_random:
        for _ in range(6):
            new_id = f"C{clone_id:03d}"
            clone_id += 1
            round_row = copy.deepcopy(
                next(r for r in tables["historical_rounds"] if r["round"] == round_id)
            )
            round_row["round"] = new_id
            round_row["competition_group"] = new_id
            new_rounds.append(round_row)
            for row in ex_by_round[round_id]:
                cloned = copy.deepcopy(row)
                cloned["round"] = new_id
                cloned["person"] = new_id + "-" + row["person"].split("-")[-1]
                new_exposures.append(cloned)
            for row in oc_by_round[round_id]:
                cloned = copy.deepcopy(row)
                cloned["round"] = new_id
                cloned["person"] = new_id + "-" + row["person"].split("-")[-1]
                new_outcomes.append(cloned)
    tables["historical_rounds"] += new_rounds
    tables["exposures"] += new_exposures
    tables["outcomes"] += new_outcomes
    return source


class SufficientControlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = Snapshot(_source_with_sufficient_controls())
        cls.evidence = HistoricalEvidence(cls.snap)

    def test_sufficient_stratum_produces_causal_increment(self):
        stratum = self.evidence.stratum_evidence("运维", "城区")
        self.assertTrue(stratum.sufficient)
        self.assertTrue(stratum.causal_available)
        self.assertGreaterEqual(stratum.high.groups, 6)
        self.assertGreaterEqual(stratum.low.groups, 6)
        self.assertIsNotNone(stratum.incremental_per_group)
        self.assertLessEqual(
            stratum.incremental_low, stratum.incremental_high
        )

    def test_priority_only_rounds_remain_descriptive(self):
        # 即便随机证据充足，仅优先批次也不产生因果增量字段。
        stratum = self.evidence.stratum_evidence("运维", "城区")
        self.assertIsNotNone(stratum.priority)
        self.assertIn("覆盖", stratum.priority.note)

    def test_portfolio_uses_range_when_evidence_sufficient(self):
        builder = PortfolioBuilder(self.snap)
        plan = builder.build_plan("充足证据方案", budget_cap=10**9)
        yunwei_urban = [
            gp
            for gp in plan.group_plans
            if gp.state.trade == "运维" and gp.state.region == "城区"
        ]
        self.assertTrue(yunwei_urban)
        for group_plan in yunwei_urban:
            self.assertTrue(group_plan.evidence.causal_available)
            # 区间上限低于可排人数时必须显式提示，不能把全部可排人都算作新增。
            if group_plan.capped_note:
                self.assertIn("往届增量区间上限仅支持", group_plan.capped_note)
            self.assertGreaterEqual(group_plan.support_high, 0.0)
        # 证据充足的组才允许出现正的可支持新增计数；本合成数据区间含 0，故为 0。
        self.assertEqual(plan.supported_incremental, 0)
        # 与证据不足分层不同，这里有真实的点估计与敏感性区间。
        self.assertTrue(
            all(gp.evidence.incremental_per_group is not None for gp in yunwei_urban)
        )


if __name__ == "__main__":
    unittest.main()
