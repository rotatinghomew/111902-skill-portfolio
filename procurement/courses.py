"""课程席位、预算与共用资源约束。

把报价截止、上课与考试时间、共用考点/教员、可退与不可退金额放进同一轮比较：

* 报价在决策时点已过期（``expires < decision_at``）的课程不能采购；
* 同一教员同一上课时间、同一考点同一考试时间视为资源冲突，不能同时排课；
* 课程席位先扣除已通知不可撤的采购，再供新组合使用；
* 同一学员的课程时段不能与其可用时间冲突，且一个学员不能同时占两门时间重叠的课。
"""

from __future__ import annotations

from dataclasses import dataclass

from .snapshot import PURCHASE_IRREVOCABLE, Quote, Snapshot
from .timeutil import parse_date


@dataclass(frozen=True)
class SeatInventory:
    quote: Quote
    total_seats: int
    reserved_irrevocable: int
    reserved_replaceable: int

    @property
    def open_seats(self) -> int:
        # 可置换采购在新方案确定前同样占着席位，只有被正式置换时才释放。
        return max(
            0,
            self.total_seats
            - self.reserved_irrevocable
            - self.reserved_replaceable,
        )


class CourseCatalog:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self._inventory: dict[str, SeatInventory] = {}
        for quote in snapshot.quotes:
            irrevocable = sum(
                1
                for purchase in snapshot.purchases
                if purchase.quote == quote.quote
                and purchase.state == PURCHASE_IRREVOCABLE
            )
            replaceable = sum(
                1
                for purchase in snapshot.purchases
                if purchase.quote == quote.quote
                and purchase.state != PURCHASE_IRREVOCABLE
            )
            self._inventory[quote.quote] = SeatInventory(
                quote=quote,
                total_seats=quote.seats,
                reserved_irrevocable=irrevocable,
                reserved_replaceable=replaceable,
            )

    def inventory(self, quote_id: str) -> SeatInventory:
        return self._inventory[quote_id]

    def purchasable_quotes(self) -> tuple[Quote, ...]:
        """决策时点仍在报价有效期内的课程。"""
        return tuple(
            quote
            for quote in self.snapshot.quotes
            if parse_date(quote.expires) >= self.snapshot.decision_at
        )

    def resource_conflicts(self, quotes: list[Quote]) -> list[str]:
        """检查教员/上课时间与考点/考试时间的共用冲突。

        同一门课被多名学员选时只占一次教员/考点资源，按课程去重后再比较。
        """
        problems: list[str] = []
        unique = {quote.quote: quote for quote in quotes}
        teacher_slots: dict[tuple[str, str], str] = {}
        room_exams: dict[tuple[str, str], str] = {}
        for quote in unique.values():
            teacher_key = (quote.teacher, quote.session)
            room_key = (quote.exam_room, quote.exam)
            if teacher_key in teacher_slots:
                problems.append(
                    f"教员 {quote.teacher} 在 {quote.session} 同时带 "
                    f"{teacher_slots[teacher_key]} 与 {quote.quote}"
                )
            else:
                teacher_slots[teacher_key] = quote.quote
            if room_key in room_exams:
                problems.append(
                    f"考点 {quote.exam_room} 在 {quote.exam} 同时安排 "
                    f"{room_exams[room_key]} 与 {quote.quote}"
                )
            else:
                room_exams[room_key] = quote.quote
        return problems

    @staticmethod
    def sessions_overlap(left: Quote, right: Quote) -> bool:
        """同一时段的课程对同一学员互斥。"""
        return left.slot == right.slot and left.session == right.session

    def cost_split(self, quote_ids: list[str]) -> tuple[int, int]:
        """返回（总支出, 其中不可退金额）。"""
        total = 0
        nonrefundable = 0
        for quote_id in quote_ids:
            quote = self.snapshot.quote(quote_id)
            total += quote.cost
            nonrefundable += quote.nonrefundable
        return total, nonrefundable
