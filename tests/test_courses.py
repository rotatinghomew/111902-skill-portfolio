"""课程席位、预算与共用资源冲突的测试。"""

import unittest
from pathlib import Path

from procurement.courses import CourseCatalog
from procurement.snapshot import Quote, load_snapshot

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "supplies.json"


class CourseCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = load_snapshot(DATA)
        cls.catalog = CourseCatalog(cls.snap)

    def test_existing_purchases_reserve_seats(self):
        inventory = self.catalog.inventory("T05")
        self.assertEqual(inventory.reserved_irrevocable, 1)
        self.assertEqual(inventory.open_seats, inventory.total_seats - 1)
        t08 = self.catalog.inventory("T08")
        self.assertEqual(t08.reserved_replaceable, 1)

    def test_resource_conflict_detects_shared_teacher_and_room(self):
        quotes = list(self.snap.quotes)
        base = quotes[0]
        clash_teacher = Quote(
            quote="X1",
            provides=base.provides,
            seats=1,
            requires=(),
            session=base.session,
            exam="2099-01-01",
            usable_from="2099-01-02",
            slot=base.slot,
            teacher=base.teacher,
            exam_room="考点99",
            cost=1,
            nonrefundable=0,
            expires="2026-12-31",
        )
        clash_room = Quote(
            quote="X2",
            provides=base.provides,
            seats=1,
            requires=(),
            session="2099-01-01",
            exam=base.exam,
            usable_from="2099-01-02",
            slot=base.slot,
            teacher="教员99",
            exam_room=base.exam_room,
            cost=1,
            nonrefundable=0,
            expires="2026-12-31",
        )
        problems = self.catalog.resource_conflicts(
            [base, clash_teacher, clash_room]
        )
        self.assertEqual(len(problems), 2)

    def test_duplicate_enrolment_in_same_quote_is_not_a_conflict(self):
        quote = self.snap.quotes[0]
        self.assertEqual(self.catalog.resource_conflicts([quote, quote]), [])

    def test_cost_split_separates_refundable_share(self):
        total, nonrefundable = self.catalog.cost_split(["T00", "T01"])
        self.assertEqual(total, 1000 + 1180)
        self.assertEqual(nonrefundable, 600 + 700)

    def test_expired_quotes_excluded_from_purchasable(self):
        ids = {q.quote for q in self.catalog.purchasable_quotes()}
        for quote in self.snap.quotes:
            if quote.expires < self.snap.decision_at.isoformat():
                self.assertNotIn(quote.quote, ids)


if __name__ == "__main__":
    unittest.main()
