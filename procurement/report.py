"""命令行报告：一轮采购比较、未填名额原因、变更影响与回查审计。

用法：
    python3 -m procurement.report [--data data/supplies.json] [--as-of 2026-12-01]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .audit import DecisionAuditor
from .changes import ChangeAnalyzer
from .eligibility import (
    REASON_MISSED_START,
    REASON_NO_ELIGIBLE_CANDIDATE,
    REASON_NO_EVIDENCE,
    REASON_SHARED_SLOT,
)
from .history import HistoricalEvidence
from .portfolio import PortfolioBuilder
from .snapshot import Snapshot, load_snapshot

REASON_TEXT = {
    REASON_NO_EVIDENCE: "没有可比较的往届证据，只能报支持范围",
    REASON_SHARED_SLOT: "多家广告在争同一个共用名额",
    REASON_MISSED_START: "学员赶不上开工",
    REASON_NO_ELIGIBLE_CANDIDATE: "没有满足企业确认资格的学员",
}

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data" / "supplies.json"


def _line(title: str) -> str:
    return f"\n=== {title} ==="


def report(snapshot: Snapshot) -> str:
    out: list[str] = []
    evidence = HistoricalEvidence(snapshot)
    builder = PortfolioBuilder(snapshot)
    auditor = DecisionAuditor(snapshot)
    changes = ChangeAnalyzer(snapshot)

    out.append(f"决策时点：{snapshot.decision_at.isoformat()}，预算：{snapshot.config.total}")
    out.append(evidence.ittt_check_note())
    out.append(
        f"成熟往届批次 {len(evidence.mature_round_ids)} 个，"
        f"观察期未满剔除 {len(evidence.excluded_immature_rounds)} 个"
    )

    out.append(_line("往届证据分层（按工种、区域）"))
    for stratum in evidence.strata_overview():
        if stratum.causal_available:
            out.append(
                f"{stratum.trade}/{stratum.locality}：高 {stratum.high.groups} 组、"
                f"低 {stratum.low.groups} 组，每小组 ITT 增量 "
                f"{stratum.incremental_per_group:.2f}，失联敏感性 "
                f"[{stratum.incremental_low:.2f}, {stratum.incremental_high:.2f}]"
            )
        else:
            priority = stratum.priority
            coverage = (
                f"，仅优先批次 {priority.groups} 组只描述覆盖"
                if priority else ""
            )
            out.append(
                f"{stratum.trade}/{stratum.locality}：仅支持范围（{stratum.reason_if_insufficient}）{coverage}"
            )

    # 同组替代单列：全组新增与学员间替代必须同时看，不能按人头重复计新增。
    total_hires = sum(g.hired for g in evidence.group_outcomes())
    total_substitution = sum(g.substitution_hires for g in evidence.group_outcomes())
    total_lost = sum(g.lost for g in evidence.group_outcomes())
    total_prequalified_hires = sum(g.prequalified_hired for g in evidence.group_outcomes())
    out.append(
        f"成熟批次合计：合格入职 {total_hires} 人，其中首轮名单外替代 "
        f"{total_substitution} 人（不计独立新增），观察期满仍失联 {total_lost} 人"
    )
    out.append(
        f"入职者中原有资格者 {total_prequalified_hires} 人、"
        f"其他 {total_hires - total_prequalified_hires} 人；"
        "培训经费只解释后者相对基线的新增，不能把原有证书学员的入职算成功劳"
    )

    plans = builder.comparison_plans()
    for plan in plans:
        out.append(_line(f"采购组合：{plan.name}"))
        out.append(
            f"总支出 {plan.total_cost}（不可退 {plan.nonrefundable_cost}）/ 预算 "
            f"{plan.budget_total}，预算内：{'是' if plan.within_budget else '否'}"
        )
        out.append(
            f"可支持新增 {plan.supported_incremental} 人；收益区间（仅证据充足组）"
            f"[{plan.support_low:.2f}, {plan.support_high:.2f}]"
        )
        if plan.replaced_purchases:
            out.append(f"已置换采购：{', '.join(plan.replaced_purchases)}")
        if plan.conflicts:
            out.append("资源冲突：" + "；".join(plan.conflicts))
        for group_plan in plan.group_plans:
            state = group_plan.state
            people = ", ".join(
                f"{a.person}({a.eligibility_state})" for a in group_plan.assignments
            ) or "无"
            out.append(
                f"- {group_plan.group} [{state.trade}/{state.region}] 名额 "
                f"{state.effective_slots}（广告 {len(state.postings)} 条共用），"
                f"已接收 {len(state.accepted_people)}，安排：{people}"
            )
            for unfilled in group_plan.unfilled:
                label = REASON_TEXT.get(unfilled.reason, unfilled.reason)
                shared = (
                    f"（广告 {'/'.join(unfilled.shared_postings)} 争同一名额）"
                    if unfilled.shared_postings
                    else ""
                )
                out.append(
                    f"    未填 x{unfilled.count}：{label}{shared} —— {unfilled.detail}"
                )
        for warning in plan.warnings:
            out.append(f"  提示：{warning}")

    out.append(_line("企业变化影响"))
    impacts = changes.impacts()
    if not impacts:
        out.append("决策时点暂无企业变化记录")
    for impact in impacts:
        out.append(
            f"{impact.group}：受影响承诺 {len(impact.affected)} 个，尚可转用名额 "
            f"{impact.reusable_slots}，可收回 {impact.recoverable_cost}，"
            f"已沉没 {impact.sunk_cost}"
        )
        for item in impact.affected:
            out.append(f"    - {item.person}（记录 {item.record}）：{item.problem}")
        for note in impact.notes:
            out.append(f"    备注：{note}")

    out.append(_line("首选方案回查审计"))
    preferred = plans[-1]
    for entry in auditor.audit(preferred):
        out.append(
            f"{entry.group}：实可用岗位 {entry.solid_positions}，条件性安排 "
            f"{entry.conditional_positions}，预计新增区间 "
            f"[{entry.projected_low:.2f}, {entry.projected_high:.2f}]"
        )
        for trace in entry.trace:
            out.append(f"    依据：{trace}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="培训采购比较与回查")
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--as-of", default=None, help="决策时点 YYYY-MM-DD")
    args = parser.parse_args(argv)
    snapshot = load_snapshot(args.data, decision_at=args.as_of)
    print(report(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
