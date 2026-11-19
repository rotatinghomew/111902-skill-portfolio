"""采购决定的回查审计。

回查一份采购决定时，把三类依据分开：

1. 实实在在可用的岗位：企业已确认资格、名额未被缩减、人员已满足资格；
2. 只在条件成立时可用的安排：待确认资格证据、在途课程、可置换席位；
3. 预计新增收益：来自随机往届 ITT 证据的增量区间，附失联敏感性与对照组数。

所有结论都能按用工组追溯到适用的资格证据与邀约强度。
"""

from __future__ import annotations

from dataclasses import dataclass

from .eligibility import IN_FLIGHT, SATISFIED, EligibilityEngine
from .portfolio import GroupPlan, Plan
from .snapshot import EVIDENCE_CONFIRMED, Snapshot


@dataclass(frozen=True)
class AuditEntry:
    group: str
    solid_positions: int
    conditional_positions: int
    projected_low: float
    projected_high: float
    evidence_basis: str
    qualification_basis: str
    trace: tuple[str, ...]


class DecisionAuditor:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self.eligibility = EligibilityEngine(snapshot)

    def audit(self, plan: Plan) -> list[AuditEntry]:
        return [self._audit_group(group_plan) for group_plan in plan.group_plans]

    def _qualification_basis(self, group_plan: GroupPlan) -> str:
        group = group_plan.group
        qualification = self.eligibility.active_qualification(group)
        revoked = self.eligibility.revoked_evidence(group)
        if qualification is None:
            tail = (
                f"被撤回证据 {', '.join(revoked)}" if revoked else "没有有效资格证据"
            )
            return f"岗位组 {group}：{tail}，不能据此许诺新增"
        alternatives = " 或 ".join(
            "(" + "、".join(option) + ")" for option in qualification.alternatives
        )
        if qualification.status == EVIDENCE_CONFIRMED:
            head = f"企业确认证据 {qualification.evidence}，资格组合 {alternatives}"
        else:
            head = (
                f"待确认证据 {qualification.evidence}（资格组合 {alternatives}），"
                "企业确认前只能算条件性安排"
            )
        if revoked:
            head += f"；历史证据 {', '.join(revoked)} 已被撤回并留痕"
        return head

    def _audit_group(self, group_plan: GroupPlan) -> AuditEntry:
        solid = sum(
            1
            for assignment in group_plan.assignments
            if assignment.eligibility_state == SATISFIED
        )
        conditional = sum(
            1
            for assignment in group_plan.assignments
            if assignment.eligibility_state == IN_FLIGHT
        )
        evidence = group_plan.evidence
        state = group_plan.state
        if evidence.causal_available:
            basis = (
                f"随机对照：高邀约 {evidence.high.groups} 组 / 低邀约 {evidence.low.groups} 组"
                f"（门槛 {evidence.min_groups_per_arm} 组/臂），ITT 每小组增量 "
                f"{evidence.incremental_per_group:.2f}，失联敏感性区间 "
                f"[{evidence.incremental_low:.2f}, {evidence.incremental_high:.2f}]，"
                f"高臂批次 {', '.join(evidence.high.rounds[:3])} 等"
            )
        else:
            basis = (
                "无因果担保："
                + (evidence.reason_if_insufficient or "证据不足")
                + "；仅优先批次只作覆盖描述，不混入因果证据"
            )
        qualification_basis = self._qualification_basis(group_plan)
        trace = (
            f"岗位广告 {', '.join(state.postings)}（共用名额）",
            f"名额 {state.advertised_slots} -> 现行 {state.effective_slots}",
            f"开工日 {state.start}",
            qualification_basis,
            basis,
        )
        return AuditEntry(
            group=group_plan.group,
            solid_positions=solid,
            conditional_positions=conditional,
            projected_low=group_plan.support_low or 0.0,
            projected_high=group_plan.support_high or 0.0,
            evidence_basis=basis,
            qualification_basis=qualification_basis,
            trace=trace,
        )
