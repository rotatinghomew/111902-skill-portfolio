"""本期学员对岗位组资格组合的满足情况与课程时程。

三类状态严格分开，绝不混报：

* ``已满足``：企业已确认的资格组合中，至少一组全部由 *已核* 且开工仍有效的证书满足；
* ``在途可满足``：现持证书不足，但按报价上课、考试并通过后，可在开工前凑齐某一
  企业已确认的资格组合（先修课必须排在后修课之前）；
* ``无企业证据``：岗位组只有待确认资格证据，或其组合被撤回，采购不能据此许诺。

待核证书（``待核``）不算已满足，只能算作待确认线索；课程报价在决策时点已过期的
不允许采购；课程产出的资格必须在开工前可用，且证书到开工日仍有效。
"""

from __future__ import annotations

from dataclasses import dataclass

from .snapshot import (
    CERT_PENDING,
    CERT_VERIFIED,
    EVIDENCE_CONFIRMED,
    PURCHASE_IRREVOCABLE,
    PURCHASE_REPLACEABLE,
    Certificate,
    Qualification,
    Quote,
    Snapshot,
)
from .timeutil import parse_date

SATISFIED = "已满足"
IN_FLIGHT = "在途可满足"
NO_EMPLOYER_EVIDENCE = "无企业证据"
ELIGIBILITY_STATES = (SATISFIED, IN_FLIGHT, NO_EMPLOYER_EVIDENCE)

# 未填名额原因码。
REASON_NO_EVIDENCE = "无可比往届证据"
REASON_SHARED_SLOT = "广告共用同一名额"
REASON_MISSED_START = "学员赶不上开工"
REASON_NO_ELIGIBLE_CANDIDATE = "无合适学员"


@dataclass(frozen=True)
class SkillHolding:
    skill: str
    verified: bool
    expires: str | None

    @property
    def valid_at_start(self) -> bool:
        return self.verified and self.expires is not None


@dataclass(frozen=True)
class CoursePath:
    """为凑齐某一资格组合所需的有序课程。"""

    alternative: tuple[str, ...]
    quotes: tuple[Quote, ...]
    missing_skills: tuple[str, ...]

    @property
    def cost(self) -> int:
        return sum(q.cost for q in self.quotes)

    @property
    def nonrefundable(self) -> int:
        return sum(q.nonrefundable for q in self.quotes)


@dataclass(frozen=True)
class CandidateEligibility:
    person: str
    group: str
    state: str
    satisfied_alternative: tuple[str, ...] | None
    paths: tuple[CoursePath, ...]
    blockers: tuple[str, ...]

    @property
    def best_path(self) -> CoursePath | None:
        if not self.paths:
            return None
        return min(self.paths, key=lambda p: (p.cost, tuple(q.quote for q in p.quotes)))


@dataclass(frozen=True)
class GroupQualificationView:
    group: str
    qualification: Qualification | None
    active: bool
    revoked_evidence: tuple[str, ...]
    candidates: tuple[CandidateEligibility, ...]


class EligibilityEngine:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self._quals_by_group: dict[str, list[Qualification]] = {}
        for qualification in snapshot.qualifications:
            self._quals_by_group.setdefault(qualification.group, []).append(
                qualification
            )
        self._quote_by_skill: dict[str, list[Quote]] = {}
        for quote in snapshot.quotes:
            if parse_date(quote.expires) >= snapshot.decision_at:
                self._quote_by_skill.setdefault(quote.provides, []).append(quote)
        self._purchases_by_person: dict[str, list] = {}
        for purchase in snapshot.purchases:
            self._purchases_by_person.setdefault(purchase.person, []).append(purchase)

    # ---- 岗位组证据 ------------------------------------------------------
    def active_qualification(self, group: str) -> Qualification | None:
        """返回当前仍有效的企业资格证据。

        被新证据通过 ``revokes`` 撤回的旧证据、以及本身已被撤回的行都不算有效；
        撤回关系留痕，可由 :meth:`revoked_evidence` 回查。
        """
        rows = self._quals_by_group.get(group, [])
        revoked_ids = {row.revokes for row in rows if row.revokes is not None}
        # 被新证据点名撤回的旧证据失效；发起撤回的新证据（revokes 非空）本身有效。
        active = [row for row in rows if row.evidence not in revoked_ids]
        if not active:
            return None
        # 同一组若有多条，取最新送达的一条（变更后的新证据）。
        return max(active, key=lambda q: parse_date(q.received))

    def revoked_evidence(self, group: str) -> tuple[str, ...]:
        """被撤回的旧证据编号；发起撤回的新证据本身仍然有效。"""
        return tuple(
            sorted(
                {
                    row.revokes
                    for row in self._quals_by_group.get(group, [])
                    if row.revokes is not None
                }
            )
        )

    # ---- 学员持有的技能 --------------------------------------------------
    def _holdings(self, person: str, start_date) -> dict[str, SkillHolding]:
        holdings: dict[str, SkillHolding] = {}
        for cert in self.snapshot.certificates_of(person):
            valid = (
                cert.evidence == CERT_VERIFIED
                and parse_date(cert.expires) >= start_date
            )
            previous = holdings.get(cert.skill)
            if previous is None or (valid and not previous.verified):
                holdings[cert.skill] = SkillHolding(
                    skill=cert.skill,
                    verified=valid,
                    expires=cert.expires,
                )
        return holdings

    def _pending_skills(self, person: str) -> set[str]:
        return {
            cert.skill
            for cert in self.snapshot.certificates_of(person)
            if cert.evidence == CERT_PENDING
        }

    # ---- 课程路径 --------------------------------------------------------
    def _feasible_quotes(self, skill: str, person: str, start_date) -> list[Quote]:
        candidate = self.snapshot.candidate(person)
        result = []
        blocked_purchases = {
            purchase.quote
            for purchase in self._purchases_by_person.get(person, [])
            if purchase.state == PURCHASE_IRREVOCABLE
        }
        for quote in self._quote_by_skill.get(skill, []):
            if quote.quote in blocked_purchases:
                continue
            if parse_date(quote.expires) < self.snapshot.decision_at:
                continue
            if parse_date(quote.usable_from) > start_date:
                continue  # 资格在开工之后才可用，赶不上开工。
            if quote.slot not in candidate.available:
                continue  # 学员时间与课程时段冲突。
            result.append(quote)
        return result

    def _build_path(
        self,
        alternative: tuple[str, ...],
        holdings: dict[str, SkillHolding],
        person: str,
        start_date,
    ) -> CoursePath | None:
        """为一组资格组合寻找可行课程序列；先修必须先于后修。

        ``holdings`` 里可能含待核/过期技能（``verified=False``），这些不能当作
        先修已满足；只有有效持有或已选课程产出的技能才计入。
        """
        missing = [
            skill
            for skill in alternative
            if not (holdings.get(skill) and holdings[skill].valid_at_start)
        ]
        # 有效持有的技能才可作为先修起点。
        acquired_prereq = {
            skill
            for skill, holding in holdings.items()
            if holding.valid_at_start
        }
        chosen: list[Quote] = []
        remaining = list(missing)
        while remaining:
            progressed = False
            for skill in sorted(remaining):
                options = [
                    q
                    for q in self._feasible_quotes(skill, person, start_date)
                    if all(req in acquired_prereq for req in q.requires)
                ]
                if not options:
                    continue
                quote = min(options, key=lambda q: (q.cost, q.session, q.quote))
                # 先修课的上课时间必须早于本课程。
                if chosen and parse_date(quote.session) <= parse_date(chosen[-1].session):
                    later_options = [
                        q for q in options
                        if parse_date(q.session) > parse_date(chosen[-1].session)
                    ]
                    if not later_options:
                        continue
                    quote = min(later_options, key=lambda q: (q.cost, q.session, q.quote))
                chosen.append(quote)
                acquired_prereq.add(skill)
                remaining.remove(skill)
                progressed = True
                break
            if not progressed:
                return None
        chosen.sort(key=lambda q: parse_date(q.session))
        # 顺序复核：相邻课程先修关系与时间先后。
        held = set(acquired_prereq)
        for quote in chosen:
            if any(req not in held for req in quote.requires):
                return None
            held.add(quote.provides)
        if any(parse_date(q.usable_from) > start_date for q in chosen):
            return None
        return CoursePath(
            alternative=alternative,
            quotes=tuple(chosen),
            missing_skills=tuple(missing),
        )

    def evaluate_candidate(self, person: str, group: str) -> CandidateEligibility:
        postings = self.snapshot.postings_of_group(group)
        if not postings:
            return CandidateEligibility(
                person=person,
                group=group,
                state=NO_EMPLOYER_EVIDENCE,
                satisfied_alternative=None,
                paths=(),
                blockers=(f"岗位组 {group} 在决策时点没有广告",),
            )
        start_date = parse_date(postings[0].start)
        qualification = self.active_qualification(group)
        holdings = self._holdings(person, start_date)
        pending = self._pending_skills(person)
        blockers: list[str] = []

        if qualification is None or qualification.status != EVIDENCE_CONFIRMED:
            state = NO_EMPLOYER_EVIDENCE
            if qualification is None:
                blockers.append("资格证据已撤回或缺失，不能据此许诺新增")
            else:
                blockers.append(
                    f"资格证据 {qualification.evidence} 仍为待确认，企业未确认"
                )
            return CandidateEligibility(
                person=person,
                group=group,
                state=state,
                satisfied_alternative=None,
                paths=(),
                blockers=tuple(blockers),
            )

        for alternative in qualification.alternatives:
            if all(
                holdings.get(skill) and holdings[skill].valid_at_start
                for skill in alternative
            ):
                return CandidateEligibility(
                    person=person,
                    group=group,
                    state=SATISFIED,
                    satisfied_alternative=alternative,
                    paths=(),
                    blockers=(),
                )

        paths: list[CoursePath] = []
        for alternative in qualification.alternatives:
            # 待核或开工前过期的证书不计入有效持有，对应技能必须出现在缺失列表，
            # 不能生成 missing 为空、quotes 也为空的“零课程路径”。
            missing = [
                skill
                for skill in alternative
                if not (
                    holdings.get(skill) and holdings[skill].valid_at_start
                )
            ]
            if not missing:
                continue
            path = self._build_path(alternative, holdings, person, start_date)
            if path is not None and path.quotes:
                paths.append(path)
        if paths:
            return CandidateEligibility(
                person=person,
                group=group,
                state=IN_FLIGHT,
                satisfied_alternative=None,
                paths=tuple(sorted(paths, key=lambda p: p.cost)),
                blockers=(),
            )

        if pending:
            blockers.append(
                "持有的待核证书不满足已确认资格：" + "、".join(sorted(pending))
            )
        blockers.append("没有在开工前凑齐企业确认资格组合的可行课程路径")
        return CandidateEligibility(
            person=person,
            group=group,
            state=NO_EMPLOYER_EVIDENCE,
            satisfied_alternative=None,
            paths=(),
            blockers=tuple(blockers),
        )

    def group_view(self, group: str) -> GroupQualificationView:
        qualification = self.active_qualification(group)
        candidate_ids = [
            candidate.person
            for candidate in self.snapshot.candidates
            if group in candidate.groups
        ]
        rows = tuple(self.evaluate_candidate(person, group) for person in candidate_ids)
        return GroupQualificationView(
            group=group,
            qualification=qualification,
            active=qualification is not None
            and qualification.status == EVIDENCE_CONFIRMED,
            revoked_evidence=self.revoked_evidence(group),
            candidates=rows,
        )
