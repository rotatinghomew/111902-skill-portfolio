"""往届批次的因果证据。

规则（对应业务约定）：

* 随机批次按 *最初分配* 的邀约强度（高/低邀约，``probability_initial``）分组做
  ITT 分析；之后是否接受、递补、参加都不能改变分组。
* 每个竞争小组是一个整体：同时给出全组合格入职数（净新增）和学员之间的替代，
  绝不能把十个候选人的变化当成十份独立新增。
* 仅优先批次没有随机对照，只报告覆盖范围，不产出因果增量。
* 观察期未满的批次不进入结局比较；成熟批次内，观察期已满仍失联与确证未入职
  分开统计，并对失联结局做敏感性区间。
* 随机对照组数不足（``min_random_groups_per_arm``）或岗位群没有可比往历时，
  只能报告支持范围，不允许用观察到的转化率给采购做担保。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .snapshot import (
    HIRED,
    LOST,
    PRIORITY_ONLY,
    RANDOM_HIGH,
    RANDOM_LOW,
    RANDOM_MODES,
    Snapshot,
)

# 失联敏感性的两个极端：失联者最终都未入职 / 失联者最终都入职。
LOST_AS_NOT_HIRED = "失联按未入职"
LOST_AS_HIRED = "失联按入职"


@dataclass(frozen=True)
class GroupOutcome:
    """一个竞争小组（一轮）在 ITT 口径下的结局。"""

    round: str
    mode: str
    trade: str
    locality: str
    cohort: int
    invitation_slots: int
    group_openings: int
    candidates: int
    hired: int
    confirmed_not_hired: int
    lost: int
    first_offer_hired: int
    non_first_offer_hired: int
    prequalified_hired: int
    mature: bool
    missing_outcomes: int = 0

    @property
    def substitution_hires(self) -> int:
        """拿到岗位的人并不在首轮名单内——同组学员间替代，不是政策新增。"""
        return self.non_first_offer_hired

    def hired_under(self, lost_assumption: str) -> int:
        if lost_assumption == LOST_AS_HIRED:
            return self.hired + self.lost
        return self.hired


@dataclass(frozen=True)
class ArmSummary:
    arm: str
    groups: int
    invitation_slots: int
    mean_hires: float
    mean_substitution: float
    mean_lost: float
    # 敏感性：每个组在两种失联假设下的入职数再聚合。
    mean_hires_lost_as_not_hired: float
    mean_hires_lost_as_hired: float
    rounds: tuple[str, ...]


@dataclass(frozen=True)
class PriorityCoverage:
    """仅优先批次没有对照，只能描述覆盖了什么。"""

    groups: int
    candidates: int
    first_offers: int
    prequalified_first_offers: int
    waitlist_fills: int
    mean_hires_observed: float
    rounds: tuple[str, ...]
    note: str = "无随机对照，仅描述覆盖范围，不作为因果增量证据"


@dataclass(frozen=True)
class StratumEvidence:
    """一个用工组可追溯到的同工种、同区域往届证据。"""

    trade: str
    locality: str
    high: ArmSummary | None
    low: ArmSummary | None
    priority: PriorityCoverage | None
    sufficient: bool
    min_groups_per_arm: int
    reason_if_insufficient: str | None
    # 高邀约相对低邀约的每小组新增（ITT 净效应）及失联敏感性区间。
    incremental_per_group: float | None = None
    incremental_low: float | None = None
    incremental_high: float | None = None
    support_only: bool = field(default=False)

    @property
    def causal_available(self) -> bool:
        return self.sufficient and self.incremental_per_group is not None


def _group_outcome(snapshot: Snapshot, round_id: str) -> GroupOutcome:
    meta = snapshot.round(round_id)
    exposures = snapshot.exposures_of(round_id)
    hired = lost = not_hired = missing = 0
    first_hired = nonfirst_hired = preq_hired = 0
    for exposure in exposures:
        outcome = snapshot.outcome(round_id, exposure.person)
        status = outcome.status if outcome is not None else None
        if status == HIRED:
            hired += 1
            if exposure.first_offer:
                first_hired += 1
            else:
                nonfirst_hired += 1
            if exposure.prequalified:
                preq_hired += 1
        elif status == LOST:
            lost += 1
        elif status is None:
            # 决策时点尚未送达的结局；成熟组不应出现，未成熟组会被整体剔除。
            missing += 1
        else:
            not_hired += 1
    return GroupOutcome(
        round=round_id,
        mode=meta.mode,
        trade=meta.trade,
        locality=meta.locality,
        cohort=meta.cohort,
        invitation_slots=meta.invitation_slots,
        group_openings=meta.group_openings,
        candidates=len(exposures),
        hired=hired,
        confirmed_not_hired=not_hired,
        lost=lost,
        first_offer_hired=first_hired,
        non_first_offer_hired=nonfirst_hired,
        prequalified_hired=preq_hired,
        mature=snapshot.is_round_mature(round_id),
        missing_outcomes=missing,
    )


def _summarize_arm(arm: str, groups: list[GroupOutcome]) -> ArmSummary:
    n = len(groups)
    slots = groups[0].invitation_slots if groups else 0

    def mean(values):
        return sum(values) / n if n else 0.0

    return ArmSummary(
        arm=arm,
        groups=n,
        invitation_slots=slots,
        mean_hires=mean([g.hired for g in groups]),
        mean_substitution=mean([g.substitution_hires for g in groups]),
        mean_lost=mean([g.lost for g in groups]),
        mean_hires_lost_as_not_hired=mean(
            [g.hired_under(LOST_AS_NOT_HIRED) for g in groups]
        ),
        mean_hires_lost_as_hired=mean(
            [g.hired_under(LOST_AS_HIRED) for g in groups]
        ),
        rounds=tuple(g.round for g in groups),
    )


def _summarize_priority(snapshot: Snapshot, groups: list[GroupOutcome]) -> PriorityCoverage:
    round_ids = tuple(g.round for g in groups)
    exposures = [
        exposure
        for round_id in round_ids
        for exposure in snapshot.exposures_of(round_id)
    ]
    return PriorityCoverage(
        groups=len(groups),
        candidates=len(exposures),
        first_offers=sum(1 for e in exposures if e.first_offer),
        prequalified_first_offers=sum(
            1 for e in exposures if e.first_offer and e.prequalified
        ),
        waitlist_fills=sum(1 for e in exposures if e.offer == "递补"),
        mean_hires_observed=sum(g.hired for g in groups) / len(groups)
        if groups
        else 0.0,
        rounds=round_ids,
    )


class HistoricalEvidence:
    """按用工组（工种、区域）组织的往届证据视图。"""

    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self._all_groups = [
            _group_outcome(snapshot, meta.round) for meta in snapshot.rounds
        ]
        self._mature = [g for g in self._all_groups if g.mature]
        self._by_stratum: dict[tuple[str, str], list[GroupOutcome]] = {}
        for group in self._mature:
            self._by_stratum.setdefault(
                (group.trade, group.locality), []
            ).append(group)
        self._evidence_cache: dict[tuple[str, str], StratumEvidence] = {}

    @property
    def mature_round_ids(self) -> tuple[str, ...]:
        return tuple(g.round for g in self._mature)

    @property
    def excluded_immature_rounds(self) -> tuple[str, ...]:
        """观察期未满、不参与结局比较的批次。"""
        return tuple(g.round for g in self._all_groups if not g.mature)

    def group_outcomes(self) -> tuple[GroupOutcome, ...]:
        return tuple(self._mature)

    def stratum_evidence(self, trade: str, locality: str) -> StratumEvidence:
        key = (trade, locality)
        if key in self._evidence_cache:
            return self._evidence_cache[key]
        groups = self._by_stratum.get(key, [])
        high_groups = [g for g in groups if g.mode == RANDOM_HIGH]
        low_groups = [g for g in groups if g.mode == RANDOM_LOW]
        priority_groups = [g for g in groups if g.mode == PRIORITY_ONLY]

        high = _summarize_arm(RANDOM_HIGH, high_groups) if high_groups else None
        low = _summarize_arm(RANDOM_LOW, low_groups) if low_groups else None
        priority = (
            _summarize_priority(self.snapshot, priority_groups)
            if priority_groups
            else None
        )

        minimum = self.snapshot.config.min_random_groups_per_arm
        sufficient = (
            high is not None
            and low is not None
            and high.groups >= minimum
            and low.groups >= minimum
        )
        reason = None
        incremental = inc_low = inc_high = None
        if not groups:
            reason = "本期岗位群在同工种、同区域没有任何可比往届批次"
        elif high is None or low is None:
            reason = "缺少高邀约或低邀约其中一个随机臂，无法形成对照"
        elif not sufficient:
            reason = (
                f"每臂成熟随机小组不足：要求至少 {minimum} 个，"
                f"实际高邀约 {high.groups} 个、低邀约 {low.groups} 个"
            )
        if sufficient:
            incremental = high.mean_hires - low.mean_hires
            # 敏感性区间：高臂与低臂在相反失联假设下取到的最宽差。
            inc_high = (
                high.mean_hires_lost_as_hired - low.mean_hires_lost_as_not_hired
            )
            inc_low = (
                high.mean_hires_lost_as_not_hired
                - low.mean_hires_lost_as_hired
            )
            inc_low, inc_high = sorted((inc_low, inc_high))
        evidence = StratumEvidence(
            trade=trade,
            locality=locality,
            high=high,
            low=low,
            priority=priority,
            sufficient=sufficient,
            min_groups_per_arm=minimum,
            reason_if_insufficient=reason,
            incremental_per_group=incremental,
            incremental_low=inc_low,
            incremental_high=inc_high,
            support_only=not sufficient,
        )
        self._evidence_cache[key] = evidence
        return evidence

    def strata_overview(self) -> list[StratumEvidence]:
        keys = sorted({(g.trade, g.locality) for g in self._all_groups})
        return [self.stratum_evidence(trade, locality) for trade, locality in keys]

    def ittt_check_note(self) -> str:
        """说明分组严格使用最初分配，未按接受/递补/参加重新分组。"""
        random_modes = set(RANDOM_MODES)
        mismatches = []
        for meta in self.snapshot.rounds:
            if meta.mode not in random_modes:
                continue
            assigned = {
                exposure.probability_initial
                for exposure in self.snapshot.exposures_of(meta.round)
            }
            if assigned != {meta.high_arm_probability if meta.mode == RANDOM_HIGH
                            else _low_probability(self.snapshot, meta.round)}:
                mismatches.append(meta.round)
        if mismatches:  # 数据若被按事后行为重分组，这里会暴露。
            return f"发现 {len(mismatches)} 个随机批次的初始分配不一致：{mismatches}"
        return "全部随机批次按 probability_initial 的最初分配入组，ITT 口径成立"


def _low_probability(snapshot: Snapshot, round_id: str) -> float:
    values = {
        exposure.probability_initial
        for exposure in snapshot.exposures_of(round_id)
        if exposure.probability_initial is not None
    }
    return next(iter(values))
