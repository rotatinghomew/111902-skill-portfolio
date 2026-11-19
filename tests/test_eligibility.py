"""资格三分类、证书有效期、先修顺序与报价截止的测试。"""

import unittest
from pathlib import Path

from procurement.eligibility import (
    IN_FLIGHT,
    NO_EMPLOYER_EVIDENCE,
    SATISFIED,
    EligibilityEngine,
)
from procurement.snapshot import load_snapshot
from procurement.timeutil import parse_date

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "supplies.json"


class EligibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = load_snapshot(DATA)
        cls.engine = EligibilityEngine(cls.snap)

    def test_three_states_never_mixed(self):
        states = set()
        for candidate in self.snap.candidates:
            for group in candidate.groups:
                row = self.engine.evaluate_candidate(candidate.person, group)
                states.add(row.state)
                if row.state == SATISFIED:
                    self.assertIsNotNone(row.satisfied_alternative)
                    self.assertEqual(row.paths, ())
                elif row.state == IN_FLIGHT:
                    self.assertTrue(row.paths)
                    self.assertTrue(all(p.quotes for p in row.paths))
                else:
                    self.assertEqual(row.paths, ())
        self.assertTrue({SATISFIED, IN_FLIGHT, NO_EMPLOYER_EVIDENCE} <= states)

    def test_pending_qualification_group_is_no_evidence(self):
        # N00 的 R0 是待确认证据，任何学员都不能被算作已满足或在途。
        view = self.engine.group_view("N00")
        self.assertFalse(view.active)
        self.assertTrue(
            all(row.state == NO_EMPLOYER_EVIDENCE for row in view.candidates)
        )

    def test_pending_certificates_do_not_count_as_satisfied(self):
        # P000 只有一张待核的安全底训，N00 待确认且 N01 需要乙类，不能算满足。
        row = self.engine.evaluate_candidate("P000", "N01")
        self.assertIn(row.state, (IN_FLIGHT, NO_EMPLOYER_EVIDENCE))
        self.assertNotEqual(row.state, SATISFIED)

    def test_inflight_path_respects_timing_and_prerequisites(self):
        for candidate in self.snap.candidates:
            for group in candidate.groups:
                row = self.engine.evaluate_candidate(candidate.person, group)
                if row.state != IN_FLIGHT:
                    continue
                start = parse_date(
                    self.snap.postings_of_group(group)[0].start
                )
                for path in row.paths:
                    sessions = [parse_date(q.session) for q in path.quotes]
                    self.assertEqual(sessions, sorted(sessions))
                    for quote in path.quotes:
                        self.assertLessEqual(
                            parse_date(quote.usable_from), start
                        )
                        self.assertGreaterEqual(
                            parse_date(quote.expires), self.snap.decision_at
                        )
                        self.assertIn(
                            quote.slot, candidate.available
                        )

    def test_expired_quote_cannot_be_purchased(self):
        # 决策时点 2026-12-01，所有 expires 早于该日的报价都要被剔除。
        purchasable = {
            q.quote for q in self.engine.snapshot.quotes
            if parse_date(q.expires) >= self.snap.decision_at
        }
        for skill, quotes in self.engine._quote_by_skill.items():
            for quote in quotes:
                self.assertIn(quote.quote, purchasable)

    def test_revoked_evidence_chain_at_later_point(self):
        later = EligibilityEngine(
            load_snapshot(DATA, decision_at="2026-12-03")
        )
        active = later.active_qualification("N01")
        self.assertIsNotNone(active)
        self.assertEqual(active.evidence, "RX")
        self.assertIn("R1", later.revoked_evidence("N01"))

    def test_certificate_must_remain_valid_until_start(self):
        # P104 的证书 2026-12-29 到期，早于所有开工日，不能算已满足。
        for group in self.snap.candidate("P104").groups:
            row = self.engine.evaluate_candidate("P104", group)
            self.assertNotEqual(row.state, SATISFIED)

    def test_pending_cert_cannot_satisfy_prerequisite(self):
        # P000 的安全底训是待核证书，不能选要求先修安全底训的 T33；
        # 只能走无先修的 T05，或把安全底训也列入在途课程的组合。
        row = self.engine.evaluate_candidate("P000", "N01")
        for path in row.paths:
            held = {
                cert.skill
                for cert in self.snap.certificates_of("P000")
                if cert.evidence == "已核"
            }
            for quote in path.quotes:
                self.assertTrue(
                    all(req in held for req in quote.requires)
                )
                held.add(quote.provides)
            self.assertNotIn("T33", [q.quote for q in path.quotes])


if __name__ == "__main__":
    unittest.main()
