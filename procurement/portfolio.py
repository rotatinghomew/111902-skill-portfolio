"""采购组合与可支持新增的判断。

一个 *方案* 是一组（学员, 课程）采购决定，外加学员到岗位组的分配。判断顺序：

1. 已被企业接收的人先占名额；已通知不可撤的席位先占课程与经费，且学员绑定不重排；
2. 同一学员在任何方案里最多填一个岗位，待确认去向预留的席位不能再许诺给别家；
3. 岗位名额按用工组共享，广告条数不放大名额；
4. 课程席位、预算、时段、教员/考点冲突、先修顺序同时校验；
5. 只有本期资格、时程 *和* 往届能支持的收益范围都合适，才计入可支持新增；
   证据不足的组只报告支持范围，不用观察转化率做担保。
"""

from __future__ import annotations

from dataclasses import dataclass

from .courses import CourseCatalog
from .eligibility import (
    IN_FLIGHT,
    NO_EMPLOYER_EVIDENCE,
    REASON_MISSED_START,
    REASON_NO_ELIGIBLE_CANDIDATE,
    REASON_NO_EVIDENCE,
    REASON_SHARED_SLOT,
    SATISFIED,
    CandidateEligibility,
    EligibilityEngine,
)
from .groups import GroupDirectory, SlotState
from .history import HistoricalEvidence, StratumEvidence
from .snapshot import (
    PURCHASE_IRREVOCABLE,
    PURCHASE_REPLACEABLE,
    Purchase,
    Snapshot,
)
from .timeutil import parse_date


@dataclass(frozen=True)
class AssignedSeat:
    person: str
    group: str
    eligibility_state: str
    quote_ids: tuple[str, ...]
    incremental: bool  # 是否计入可支持新增（已接收的人不算本期新增）


@dataclass(frozen=True)
class UnfilledSlot:
    group: str
    reason: str
    detail: str
    count: int = 1
    shared_postings: tuple[str, ...] = ()  # 多家广告争同一名额时的广告编号


@dataclass(frozen=True)
class GroupPlan:
    group: str
    state: SlotState
    evidence: StratumEvidence
    assignments: tuple[AssignedSeat, ...]
    unfilled: tuple[UnfilledSlot, ...]
    support_low: float | None
    support_high: float | None
    supported_incremental: int
    capped_note: str | None


@dataclass(frozen=True)
class PurchaseLine:
    person: str
    quote: str
    state: str
    cost: int
    nonrefundable: int


@dataclass(frozen=True)
class Plan:
    name: str
    lines: tuple[PurchaseLine, ...]
    group_plans: tuple[GroupPlan, ...]
    total_cost: int
    nonrefundable_cost: int
    budget_total: int
    within_budget: bool
    supported_incremental: int
    support_low: float
    support_high: float
    conflicts: tuple[str, ...]
    replaced_purchases: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def feasible(self) -> bool:
        return self.within_budget and not self.conflicts


class PortfolioBuilder:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self.groups = GroupDirectory(snapshot)
        self.eligibility = EligibilityEngine(snapshot)
        self.catalog = CourseCatalog(snapshot)
        self.evidence = HistoricalEvidence(snapshot)
        self._accepted = self.groups.accepted_index()
        self._irrevocable = {
            purchase.person: purchase
            for purchase in self.groups.irrevocable_purchases()
        }
        self._replaceable = {
            purchase.person: purchase
            for purchase in self.groups.replaceable_purchases()
        }

    # ---- 往届收益范围 ----------------------------------------------------
    def _support_range(self, group: str, open_slots: int):
        """返回 (low, high, sufficient)。

        证据充足：以高/低邀约 ITT 每小组增量的失联敏感性区间，按本组名额与
        往届 4 个名额的比例缩放，并截断到实际可填名额。
        证据不足：只能给 0..可填名额的支持范围，且明确不是转化率担保。
        """
        state = self.groups.slot_state(group)
        evidence = self.evidence.stratum_evidence(state.trade, state.region)
        if not evidence.causal_available:
            return 0.0, float(open_slots), False
        scale = state.effective_slots / 4.0  # 往届每个竞争小组固定 4 个名额
        low = max(0.0, evidence.incremental_low * scale)
        high = max(low, evidence.incremental_high * scale)
        high = min(high, float(open_slots))
        low = min(low, high)
        return low, high, True

    # ---- 学员可用性 ------------------------------------------------------
    def _blocked_people(self) -> set[str]:
        """已被企业接收或持有不可撤席位的学员，不参与新分配。"""
        return set(self._accepted) | set(self._irrevocable)

    def _candidate_rows(self, group: str) -> list[CandidateEligibility]:
        view = self.eligibility.group_view(group)
        blocked = self._blocked_people()
        return [row for row in view.candidates if row.person not in blocked]

    # ---- 组内匹配 --------------------------------------------------------
    def _fill_group(
        self,
        group: str,
        seat_usage: dict[str, int],
        used_people: set[str],
        lines: list[PurchaseLine],
        allow_new_purchase: bool,
        budget_cap: int | None = None,
        budget_blocked: list[str] | None = None,
    ) -> GroupPlan:
        state = self.groups.slot_state(group)
        evidence = self.evidence.stratum_evidence(state.trade, state.region)
        open_slots = state.open_slots
        support_low, support_high, sufficient = self._support_range(group, open_slots)

        rows = [
            row
            for row in self._candidate_rows(group)
            if row.person not in used_people
        ]
        # 已满足的学员优先（不需要新花培训费），其次按最便宜的在途路径。
        rows.sort(
            key=lambda row: (
                0 if row.state == SATISFIED else 1 if row.state == IN_FLIGHT else 2,
                row.best_path.cost if row.best_path else 10**9,
                row.person,
            )
        )

        assignments: list[AssignedSeat] = []
        # 跳过原因 -> 学员名单，最后聚合成未填名额，避免逐人刷屏。
        skips: dict[str, list[str]] = {}
        unfilled: list[UnfilledSlot] = []
        remaining = open_slots
        for row in rows:
            if remaining == 0:
                break
            if row.state == NO_EMPLOYER_EVIDENCE:
                skips.setdefault(REASON_NO_ELIGIBLE_CANDIDATE, []).append(row.person)
                continue
            quote_ids: tuple[str, ...] = ()
            if row.state == IN_FLIGHT:
                if not allow_new_purchase:
                    # 基准方案不新增采购：在途学员无法靠新课程补齐。
                    skips.setdefault(REASON_NO_ELIGIBLE_CANDIDATE, []).append(row.person)
                    continue
                path = row.best_path
                if not self._seats_available(path.quotes, seat_usage):
                    skips.setdefault(REASON_NO_ELIGIBLE_CANDIDATE, []).append(row.person)
                    continue
                committed = sum(line.cost for line in lines)
                if (
                    budget_cap is not None
                    and committed + path.cost > budget_cap
                ):
                    skips.setdefault(REASON_NO_ELIGIBLE_CANDIDATE, []).append(row.person)
                    if budget_blocked is not None:
                        budget_blocked.append(
                            f"{group}/{row.person} 课程 {','.join(q.quote for q in path.quotes)}"
                            f" 需 {path.cost}，剩余预算 {budget_cap - committed} 不足"
                        )
                    continue
                if not self._person_schedule_ok(row.person, path.quotes, lines):
                    skips.setdefault(REASON_MISSED_START, []).append(row.person)
                    continue
                quote_ids = tuple(q.quote for q in path.quotes)
                self._consume(path.quotes, seat_usage, lines, row.person)
            assignments.append(
                AssignedSeat(
                    person=row.person,
                    group=group,
                    eligibility_state=row.state,
                    quote_ids=quote_ids,
                    incremental=True,
                )
            )
            used_people.add(row.person)
            remaining -= 1

        filled = len(assignments)
        # 可支持新增：只在证据充足时给点估计口径的计数，并受证据区间约束。
        if sufficient:
            supported = min(filled, int(round(support_high)))
            capped_note = None
            if filled > support_high:
                capped_note = (
                    f"实际可排 {filled} 人，但往届增量区间上限仅支持 {support_high:.2f} 人"
                )
        else:
            supported = 0
            capped_note = "往届对照不足，仅报告支持范围，不担保转化率"

        # 剩余名额按原因归并。“赶不上开工”是学员层面最硬的时程约束，优先标注；
        # 其次是组层面的“无可比往届证据”，让经办人区分能力问题与证据问题。
        shared = state.postings if state.slot_sharing else ()
        timing_skips = skips.get(REASON_MISSED_START)
        if remaining > 0 and timing_skips:
            take = min(remaining, len(timing_skips))
            sample = "、".join(timing_skips[:3]) + (
                "…" if len(timing_skips) > 3 else ""
            )
            unfilled.append(
                UnfilledSlot(
                    group=group,
                    reason=REASON_MISSED_START,
                    detail=f"{take} 个名额受此限制，涉及学员 {sample}",
                    count=take,
                    shared_postings=shared,
                )
            )
            remaining -= take
        if remaining > 0 and evidence.support_only:
            # 证据不足但根因是时程的，仍标“赶不上开工”，避免把能力问题误报成证据问题。
            late_reason = (
                REASON_MISSED_START
                if self._only_late_courses_available(group, state)
                else REASON_NO_EVIDENCE
            )
            unfilled.append(
                UnfilledSlot(
                    group=group,
                    reason=late_reason,
                    detail=self._unfilled_detail(late_reason, state, evidence),
                    count=remaining,
                    shared_postings=shared,
                )
            )
            remaining = 0
        reason_order = [
            REASON_NO_ELIGIBLE_CANDIDATE,
            REASON_SHARED_SLOT,
        ]
        for reason in reason_order:
            if remaining == 0:
                break
            people = skips.get(reason)
            if people:
                take = min(remaining, len(people))
                sample = "、".join(people[:3]) + ("…" if len(people) > 3 else "")
                detail = f"{take} 个名额受此限制，涉及学员 {sample}"
                unfilled.append(
                    UnfilledSlot(
                        group=group,
                        reason=reason,
                        detail=detail,
                        count=take,
                        shared_postings=shared,
                    )
                )
                remaining -= take
        if remaining > 0:
            reason = self._unfilled_reason(group, rows, evidence, state)
            unfilled.append(
                UnfilledSlot(
                    group=group,
                    reason=reason,
                    detail=self._unfilled_detail(reason, state, evidence),
                    count=remaining,
                    shared_postings=shared,
                )
            )
        return GroupPlan(
            group=group,
            state=state,
            evidence=evidence,
            assignments=tuple(assignments),
            unfilled=tuple(unfilled),
            support_low=support_low,
            support_high=support_high,
            supported_incremental=supported,
            capped_note=capped_note,
        )

    def _only_late_courses_available(self, group: str, state: SlotState) -> bool:
        """组内是否存在“技能只能由晚于开工的课程提供”的情况。

        资格引擎会把这类学员归到无企业证据，但业务上经办人需要知道根因是时程，
        因此这里直接从课程目录与资格组合再判断一次。
        """
        from .timeutil import parse_date

        qualification = self.eligibility.active_qualification(group)
        if qualification is None:
            return False
        start = parse_date(state.start)
        for alternative in qualification.alternatives:
            for skill in alternative:
                quotes = self.eligibility._quote_by_skill.get(skill, [])
                if not quotes:
                    continue
                if all(parse_date(q.usable_from) > start for q in quotes):
                    return True
        return False

    def _unfilled_reason(self, group, rows, evidence: StratumEvidence, state: SlotState) -> str:
        """未填名额原因，按业务上最可操作的一类归并：

        赶不上开工 > 无企业证据/无合适学员 > 广告共用名额 > 无可比往届证据。
        """
        from .timeutil import parse_date

        eligible = [r for r in rows if r.state != NO_EMPLOYER_EVIDENCE]
        qualification = self.eligibility.active_qualification(group)
        if qualification is None or qualification.status != "企业确认":
            return REASON_NO_EVIDENCE
        if any(
            r.state == IN_FLIGHT
            and r.best_path is not None
            and r.best_path.quotes
            and parse_date(r.best_path.quotes[-1].usable_from)
            > parse_date(state.start)
            for r in rows
        ) or self._only_late_courses_available(group, state):
            return REASON_MISSED_START
        if not eligible:
            return REASON_NO_ELIGIBLE_CANDIDATE
        if state.slot_sharing:
            return REASON_SHARED_SLOT
        if evidence.support_only:
            return REASON_NO_EVIDENCE
        return REASON_NO_ELIGIBLE_CANDIDATE

    @staticmethod
    def _unfilled_detail(reason, state: SlotState, evidence: StratumEvidence) -> str:
        if reason == REASON_NO_EVIDENCE:
            return evidence.reason_if_insufficient or "无可比往届证据"
        if reason == REASON_SHARED_SLOT:
            return f"广告 {', '.join(state.postings)} 共用 {state.effective_slots} 个名额"
        if reason == REASON_MISSED_START:
            return f"开工日 {state.start} 早于在途课程的可用时间"
        return "剩余学员都不满足企业确认的资格组合"

    # ---- 席位与排课 ------------------------------------------------------
    def _seats_available(self, quotes, seat_usage: dict[str, int]) -> bool:
        for quote in quotes:
            inventory = self.catalog.inventory(quote.quote)
            if seat_usage.get(quote.quote, 0) >= inventory.open_seats:
                return False
        return True

    def _person_schedule_ok(self, person: str, quotes, lines: list[PurchaseLine]) -> bool:
        candidate = self.snapshot.candidate(person)
        owned = [
            self.snapshot.quote(line.quote)
            for line in lines
            if line.person == person
        ]
        for quote in quotes:
            if quote.slot not in candidate.available:
                return False
            for existing in owned:
                if self.catalog.sessions_overlap(existing, quote):
                    return False
        return True

    def _consume(self, quotes, seat_usage, lines: list[PurchaseLine], person: str):
        for quote in quotes:
            seat_usage[quote.quote] = seat_usage.get(quote.quote, 0) + 1
            lines.append(
                PurchaseLine(
                    person=person,
                    quote=quote.quote,
                    state=PURCHASE_REPLACEABLE,
                    cost=quote.cost,
                    nonrefundable=quote.nonrefundable,
                )
            )

    # ---- 整轮方案 --------------------------------------------------------
    def build_plan(
        self,
        name: str,
        replace: set[str] | None = None,
        allow_new_purchase: bool = True,
        budget_cap: int | None = None,
    ) -> Plan:
        """构造一轮采购方案。

        replace: 选择置换的“已签可置换”采购 quote 集合；不可撤采购不能置换。
        allow_new_purchase: 基准方案置 False，只保留既有采购、不新增课程。
        budget_cap: 给定总支出上限时，超出上限的课程不再采购，名额转未填。
        """
        replace = set(replace or ())
        invalid = replace - {
            purchase.quote
            for purchase in self.groups.replaceable_purchases()
        }
        if invalid:
            raise ValueError(f"只能置换已签可置换采购，收到：{sorted(invalid)}")

        lines: list[PurchaseLine] = []
        seat_usage: dict[str, int] = {}
        used_people: set[str] = set(self._accepted) | set(self._irrevocable)

        # 既有采购先入账。
        for purchase in self.snapshot.purchases:
            quote = self.snapshot.quote(purchase.quote)
            if purchase.state == PURCHASE_REPLACEABLE and purchase.quote in replace:
                continue  # 释放席位与经费。
            lines.append(
                PurchaseLine(
                    person=purchase.person,
                    quote=purchase.quote,
                    state=purchase.state,
                    cost=quote.cost,
                    nonrefundable=quote.nonrefundable,
                )
            )
            if purchase.state == PURCHASE_IRREVOCABLE:
                seat_usage[purchase.quote] = seat_usage.get(purchase.quote, 0) + 1

        group_plans: list[GroupPlan] = []
        budget_blocked: list[str] = []
        for group in self.groups.groups:
            group_plans.append(
                self._fill_group(
                    group,
                    seat_usage,
                    used_people,
                    lines,
                    allow_new_purchase,
                    budget_cap=budget_cap,
                    budget_blocked=budget_blocked,
                )
            )

        total_cost = sum(line.cost for line in lines)
        nonrefundable = sum(line.nonrefundable for line in lines)
        budget = self.snapshot.config.total
        conflicts = self.catalog.resource_conflicts(
            [self.snapshot.quote(line.quote) for line in lines]
        )
        warnings = []
        for group_plan in group_plans:
            if group_plan.capped_note:
                warnings.append(f"[{group_plan.group}] {group_plan.capped_note}")
        for note in budget_blocked:
            warnings.append(f"[预算受限] {note}")

        support_low = float(
            sum(
                gp.support_low or 0
                for gp in group_plans
                if gp.evidence.causal_available
            )
        )
        support_high = float(
            sum(
                gp.support_high or 0
                for gp in group_plans
                if gp.evidence.causal_available
            )
        )
        return Plan(
            name=name,
            lines=tuple(lines),
            group_plans=tuple(group_plans),
            total_cost=total_cost,
            nonrefundable_cost=nonrefundable,
            budget_total=budget,
            within_budget=total_cost <= budget,
            supported_incremental=sum(gp.supported_incremental for gp in group_plans),
            support_low=support_low,
            support_high=support_high,
            conflicts=tuple(conflicts),
            replaced_purchases=tuple(sorted(replace)),
            warnings=tuple(warnings),
        )

    def baseline_plan(self) -> Plan:
        """不新增任何采购，只看既有承诺与已满足资格的自然可填名额。"""
        return self.build_plan(
            "基准：不新增采购", allow_new_purchase=False
        )

    def comparison_plans(self) -> list[Plan]:
        """同一轮里并列比较的几套购课组合。"""
        budget = self.snapshot.config.total
        plans = [self.baseline_plan()]
        replaceable_quotes = {
            purchase.quote for purchase in self.groups.replaceable_purchases()
        }
        plans.append(
            self.build_plan(
                "方案甲：预算内最大覆盖（保留已签采购）", budget_cap=budget
            )
        )
        if replaceable_quotes:
            plans.append(
                self.build_plan(
                    "方案乙：置换可撤采购后的预算内组合",
                    replace=replaceable_quotes,
                    budget_cap=budget,
                )
            )
        plans.append(
            self.build_plan("对照：不设预算上限的全量补齐（仅看需要多少钱）")
        )
        return plans
