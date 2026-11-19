"""企业变化的影响分析与采购决定回查。

企业缩减名额、改开工日或撤回资格补证后：

* 原始广告、原接收承诺仍然可查，已被接收的人不会被无声挪到别处；
* 确实不能履约时，列出受影响的承诺，并把 *尚可转用的名额*、*能收回的金额*
  （可退部分）与 *已沉没成本*（不可退部分）分开；
* 带着剩下的约束重新判断可买的组合。
"""

from __future__ import annotations

from dataclasses import dataclass

from .eligibility import EligibilityEngine
from .groups import GroupDirectory
from .snapshot import Acceptance, Snapshot
from .timeutil import parse_date


@dataclass(frozen=True)
class AffectedCommitment:
    person: str
    group: str
    record: str
    quote: str | None
    purchase_state: str | None
    problem: str


@dataclass(frozen=True)
class ChangeImpact:
    group: str
    affected: tuple[AffectedCommitment, ...]
    reusable_slots: int
    recoverable_cost: int
    sunk_cost: int
    notes: tuple[str, ...]


class ChangeAnalyzer:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self.groups = GroupDirectory(snapshot)
        self.eligibility = EligibilityEngine(snapshot)
        self._purchase_by_person = {
            purchase.person: purchase for purchase in snapshot.purchases
        }

    def impacts(self) -> list[ChangeImpact]:
        results: list[ChangeImpact] = []
        for group in sorted({change.group for change in self.snapshot.changes}):
            state = self.groups.slot_state(group)
            acceptances = self.groups.acceptances(group)
            affected: list[AffectedCommitment] = []
            notes: list[str] = []

            # 1) 名额缩减：超出新名额的接收承诺无法履约，原承诺保留可查。
            overflow = max(0, len(acceptances) - state.effective_slots)
            kept = acceptances[: state.effective_slots]
            dropped = acceptances[len(acceptances) - overflow :] if overflow else []
            for acceptance in dropped:
                affected.append(
                    self._commitment(
                        acceptance,
                        problem=(
                            f"岗位组 {group} 名额由 {state.advertised_slots} 缩减至 "
                            f"{state.effective_slots}，该接收承诺无名额可履约"
                        ),
                    )
                )

            # 2) 开工日变化：在途课程赶不上新开工日的承诺受影响。
            if state.start_moved:
                notes.append(
                    f"开工日由 {state.original_start} 调整为 {state.start}，原广告保留可查"
                )
                for acceptance in kept:
                    purchase = self._purchase_by_person.get(acceptance.person)
                    if purchase is None:
                        continue
                    quote = self.snapshot.quote(purchase.quote)
                    if parse_date(quote.usable_from) > parse_date(state.start):
                        affected.append(
                            self._commitment(
                                acceptance,
                                problem=(
                                    f"开工日改为 {state.start}，课程 {quote.quote} "
                                    f"到 {quote.usable_from} 才可用，赶不上开工"
                                ),
                            )
                        )

            # 3) 资格证据撤回：新证据未覆盖或仅待确认时，保留的接收承诺失去企业依据。
            qualification = self.eligibility.active_qualification(group)
            revoked = self.eligibility.revoked_evidence(group)
            if revoked:
                current = (
                    f"现行证据 {qualification.evidence}（{qualification.status}）"
                    if qualification
                    else "无现行证据"
                )
                notes.append(
                    f"旧资格证据 {', '.join(revoked)} 已被撤回并留痕；{current}"
                )
            if qualification is None:
                for acceptance in kept:
                    affected.append(
                        self._commitment(
                            acceptance,
                            problem=f"岗位组 {group} 已无有效企业资格证据，承诺失去依据",
                        )
                    )
            else:
                # 现行证据仍有效，但组合可能已变化：原接收人若不再满足，承诺受影响。
                for acceptance in kept:
                    eligibility = self.eligibility.evaluate_candidate(
                        acceptance.person, group
                    )
                    if eligibility.state == "无企业证据":
                        affected.append(
                            self._commitment(
                                acceptance,
                                problem=(
                                    f"现行证据 {qualification.evidence} 的资格组合变化后，"
                                    f"{acceptance.person} 已无法在开工前满足"
                                ),
                            )
                        )

            recoverable, sunk = self._money(affected)
            reusable = max(0, state.effective_slots - len(kept))
            results.append(
                ChangeImpact(
                    group=group,
                    affected=tuple(affected),
                    reusable_slots=reusable,
                    recoverable_cost=recoverable,
                    sunk_cost=sunk,
                    notes=tuple(notes),
                )
            )
        return results

    def _commitment(self, acceptance: Acceptance, problem: str) -> AffectedCommitment:
        purchase = self._purchase_by_person.get(acceptance.person)
        return AffectedCommitment(
            person=acceptance.person,
            group=acceptance.group,
            record=acceptance.record,
            quote=purchase.quote if purchase else None,
            purchase_state=purchase.state if purchase else None,
            problem=problem,
        )

    def _money(self, affected: list[AffectedCommitment]) -> tuple[int, int]:
        """受影响承诺涉及的采购，可退与不可退金额分开。"""
        recoverable = sunk = 0
        seen: set[str] = set()
        for item in affected:
            if not item.quote or item.quote in seen:
                continue
            seen.add(item.quote)
            quote = self.snapshot.quote(item.quote)
            sunk += quote.nonrefundable
            recoverable += quote.cost - quote.nonrefundable
        return recoverable, sunk
