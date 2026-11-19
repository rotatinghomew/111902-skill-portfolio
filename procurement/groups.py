"""岗位组、共用名额与占用。

一个用工组的多条广告共用同一批名额，绝不能把 N00-A 与 N00-B 当成两批独立岗位。
企业变化（缩减名额、改开工日、撤回资格补证）按送达顺序覆盖原值，但原始广告与原
确认保留可查；已被企业接收的人不会被无声挪走。
"""

from __future__ import annotations

from dataclasses import dataclass

from .snapshot import (
    PURCHASE_IRREVOCABLE,
    Acceptance,
    GroupChange,
    Posting,
    Purchase,
    Snapshot,
)
from .timeutil import parse_date


@dataclass(frozen=True)
class SlotState:
    group: str
    postings: tuple[str, ...]
    region: str
    trade: str
    advertised_slots: int
    effective_slots: int
    start: str
    original_start: str
    accepted_people: tuple[str, ...]
    irrevocable_seats: tuple[str, ...]  # 已通知不可撤的采购席位（学员维度）
    changes: tuple[GroupChange, ...]
    slot_sharing: bool

    @property
    def open_slots(self) -> int:
        """扣除企业已接收的人之后仍可安排的名额。"""
        return max(0, self.effective_slots - len(self.accepted_people))

    @property
    def reduced(self) -> bool:
        return self.effective_slots < self.advertised_slots

    @property
    def start_moved(self) -> bool:
        return self.start != self.original_start


class GroupDirectory:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self._groups: dict[str, list[Posting]] = {}
        for posting in snapshot.postings:
            self._groups.setdefault(posting.opening_group, []).append(posting)
        self._changes: dict[str, list[GroupChange]] = {}
        for change in snapshot.changes:
            self._changes.setdefault(change.group, []).append(change)
        for rows in self._changes.values():
            rows.sort(key=lambda c: parse_date(c.received))
        self._acceptances: dict[str, list[Acceptance]] = {}
        for acceptance in snapshot.acceptances:
            self._acceptances.setdefault(acceptance.group, []).append(acceptance)
        self._purchases = list(snapshot.purchases)

    @property
    def groups(self) -> tuple[str, ...]:
        return tuple(sorted(self._groups))

    def postings(self, group: str) -> tuple[Posting, ...]:
        return tuple(self._groups.get(group, ()))

    def changes(self, group: str) -> tuple[GroupChange, ...]:
        return tuple(self._changes.get(group, ()))

    def acceptances(self, group: str) -> tuple[Acceptance, ...]:
        return tuple(self._acceptances.get(group, ()))

    def slot_state(self, group: str) -> SlotState:
        postings = self._groups[group]
        head = postings[0]
        # 多条广告共用名额：广告上的 slots 相同，名额只算一次。
        advertised = head.slots
        if any(posting.slots != advertised for posting in postings):
            raise ValueError(f"岗位组 {group} 的广告名额不一致，需要人工核对")
        if any(
            (posting.start, posting.region, posting.trade)
            != (head.start, head.region, head.trade)
            for posting in postings
        ):
            raise ValueError(f"岗位组 {group} 的广告关键信息不一致，需要人工核对")

        effective_slots = advertised
        effective_start = head.start
        for change in self._changes.get(group, []):
            effective_slots = change.slots
            effective_start = change.start

        accepted = tuple(
            acceptance.person for acceptance in self._acceptances.get(group, ())
        )
        irrevocable = tuple(
            sorted(
                {
                    purchase.person
                    for purchase in self._purchases
                    if purchase.state == PURCHASE_IRREVOCABLE
                }
            )
        )
        return SlotState(
            group=group,
            postings=tuple(p.posting for p in postings),
            region=head.region,
            trade=head.trade,
            advertised_slots=advertised,
            effective_slots=effective_slots,
            start=effective_start,
            original_start=head.start,
            accepted_people=accepted,
            irrevocable_seats=irrevocable,
            changes=self.changes(group),
            slot_sharing=len(postings) > 1,
        )

    def all_slot_states(self) -> tuple[SlotState, ...]:
        return tuple(self.slot_state(group) for group in self.groups)

    def accepted_index(self) -> dict[str, Acceptance]:
        """学员 -> 已被企业接收的承诺；同一学员只允许有一个接收去向。"""
        index: dict[str, Acceptance] = {}
        for acceptance in self.snapshot.acceptances:
            if acceptance.person in index:
                raise ValueError(
                    f"学员 {acceptance.person} 已有接收承诺，不能重复占用名额"
                )
            index[acceptance.person] = acceptance
        return index

    def irrevocable_purchases(self) -> tuple[Purchase, ...]:
        return tuple(
            purchase
            for purchase in self._purchases
            if purchase.state == PURCHASE_IRREVOCABLE
        )

    def replaceable_purchases(self) -> tuple[Purchase, ...]:
        return tuple(
            purchase
            for purchase in self._purchases
            if purchase.state != PURCHASE_IRREVOCABLE
        )
