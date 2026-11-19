"""决策时点快照。

采购员在 ``decision_at`` 只能看到 ``received <= decision_at`` 的记录；
更晚送达的广告、变化、采购、结局一律不进入本次判断。快照一旦构造即不可变，
后续广告更新不会改动旧快照，保证同一份采购决定可以被原样回查。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .timeutil import days_between, parse_date, to_date

RANDOM_HIGH = "高邀约"
RANDOM_LOW = "低邀约"
PRIORITY_ONLY = "仅优先"
RANDOM_MODES = (RANDOM_HIGH, RANDOM_LOW)

HIRED = "合格入职"
CONFIRMED_NOT_HIRED = "确证未入职"
LOST = "失联"

EVIDENCE_CONFIRMED = "企业确认"
EVIDENCE_PENDING = "待确认"
CERT_VERIFIED = "已核"
CERT_PENDING = "待核"

PURCHASE_IRREVOCABLE = "已通知不可撤"
PURCHASE_REPLACEABLE = "已签可置换"


@dataclass(frozen=True)
class HistoricalRound:
    round: str
    competition_group: str
    cohort: int
    trade: str
    locality: str
    asof: str
    horizon_days: int
    mode: str
    high_arm_probability: float | None
    invitation_slots: int
    group_openings: int

    @property
    def maturity_date(self):
        return parse_date(self.asof) + timedelta(days=self.horizon_days)

    @property
    def is_random(self) -> bool:
        return self.mode in RANDOM_MODES


@dataclass(frozen=True)
class Exposure:
    round: str
    person: str
    prequalified: bool
    preference: int
    first_offer: bool
    offer: str
    attended: bool
    probability_initial: float | None


@dataclass(frozen=True)
class Outcome:
    round: str
    person: str
    received: str
    status: str


@dataclass(frozen=True)
class Posting:
    posting: str
    opening_group: str
    region: str
    trade: str
    slots: int
    start: str
    received: str


@dataclass(frozen=True)
class Qualification:
    evidence: str
    group: str
    alternatives: tuple[tuple[str, ...], ...]
    status: str
    received: str
    revokes: str | None


@dataclass(frozen=True)
class GroupChange:
    group: str
    slots: int
    start: str
    received: str


@dataclass(frozen=True)
class Candidate:
    person: str
    available: tuple[str, ...]
    groups: tuple[str, ...]
    region: str


@dataclass(frozen=True)
class Certificate:
    person: str
    skill: str
    evidence: str
    expires: str


@dataclass(frozen=True)
class Quote:
    quote: str
    provides: str
    seats: int
    requires: tuple[str, ...]
    session: str
    exam: str
    usable_from: str
    slot: str
    teacher: str
    exam_room: str
    cost: int
    nonrefundable: int
    expires: str


@dataclass(frozen=True)
class Purchase:
    quote: str
    person: str
    state: str
    received: str


@dataclass(frozen=True)
class Acceptance:
    person: str
    group: str
    at: str
    record: str


@dataclass(frozen=True)
class ProcurementConfig:
    total: int
    decision_at: str
    min_random_groups_per_arm: int
    confidence: float


def _freeze_alternatives(value):
    return tuple(tuple(option) for option in value)


class Snapshot:
    """某个决策时点看到的全部资料。"""

    def __init__(self, source: dict, decision_at: str | None = None):
        tables = source["tables"]
        cfg_row = tables["procurement"][0]
        self.decision_at = to_date(decision_at or cfg_row["decision_at"])
        self.config = ProcurementConfig(
            total=cfg_row["total"],
            decision_at=cfg_row["decision_at"],
            min_random_groups_per_arm=cfg_row["min_random_groups_per_arm"],
            confidence=cfg_row["confidence"],
        )
        self.synthetic = bool(source.get("synthetic", False))

        def known(rows):
            return [r for r in rows if parse_date(r["received"]) <= self.decision_at]

        self.rounds = [HistoricalRound(**r) for r in tables["historical_rounds"]]
        self.exposures = [Exposure(**r) for r in tables["exposures"]]
        self.outcomes = [Outcome(**r) for r in known(tables["outcomes"])]
        self.postings = [Posting(**r) for r in known(tables["postings"])]
        self.qualifications = [
            Qualification(
                evidence=r["evidence"],
                group=r["group"],
                alternatives=_freeze_alternatives(r["alternatives"]),
                status=r["status"],
                received=r["received"],
                revokes=r["revokes"],
            )
            for r in known(tables["qualifications"])
        ]
        self.changes = [GroupChange(**r) for r in known(tables["changes"])]
        self.candidates = [
            Candidate(
                person=r["person"],
                available=tuple(r["available"]),
                groups=tuple(r["groups"]),
                region=r["region"],
            )
            for r in tables["candidates"]
        ]
        # 证书没有 received 字段，决策时点以资料本身为准；待核证书另由资格模块区分。
        self.certificates = [Certificate(**r) for r in tables["certificates"]]
        self.quotes = [
            Quote(
                quote=r["quote"],
                provides=r["provides"],
                seats=r["seats"],
                requires=tuple(r["requires"]),
                session=r["session"],
                exam=r["exam"],
                usable_from=r["usable_from"],
                slot=r["slot"],
                teacher=r["teacher"],
                exam_room=r["exam_room"],
                cost=r["cost"],
                nonrefundable=r["nonrefundable"],
                expires=r["expires"],
            )
            for r in tables["quotes"]
        ]
        self.purchases = [Purchase(**r) for r in known(tables["purchases"])]
        self.acceptances = [
            Acceptance(**r)
            for r in tables["accepted"]
            if parse_date(r["at"]) <= self.decision_at
        ]

        self._round_by_id = {r.round: r for r in self.rounds}
        self._exposures_by_round: dict[str, list[Exposure]] = {}
        for exposure in self.exposures:
            self._exposures_by_round.setdefault(exposure.round, []).append(exposure)
        self._outcome_by_person: dict[tuple[str, str], Outcome] = {
            (o.round, o.person): o for o in self.outcomes
        }
        self._quotes_by_id = {q.quote: q for q in self.quotes}
        self._candidates_by_id = {c.person: c for c in self.candidates}
        self._certs_by_person: dict[str, list[Certificate]] = {}
        for cert in self.certificates:
            self._certs_by_person.setdefault(cert.person, []).append(cert)
        self._postings_by_group: dict[str, list[Posting]] = {}
        for posting in self.postings:
            self._postings_by_group.setdefault(posting.opening_group, []).append(posting)

    # ---- 访问器 ----------------------------------------------------------
    def round(self, round_id: str) -> HistoricalRound:
        return self._round_by_id[round_id]

    def exposures_of(self, round_id: str) -> list[Exposure]:
        return list(self._exposures_by_round.get(round_id, ()))

    def outcome(self, round_id: str, person: str) -> Outcome | None:
        return self._outcome_by_person.get((round_id, person))

    def quote(self, quote_id: str) -> Quote:
        return self._quotes_by_id[quote_id]

    def candidate(self, person: str) -> Candidate:
        return self._candidates_by_id[person]

    def certificates_of(self, person: str) -> list[Certificate]:
        return list(self._certs_by_person.get(person, ()))

    def postings_of_group(self, group: str) -> list[Posting]:
        return list(self._postings_by_group.get(group, ()))

    # ---- 结局成熟度 ------------------------------------------------------
    def is_round_mature(self, round_id: str) -> bool:
        """观察期已满：该组所有可见结局都在成熟日之前送达。

        未成熟批次不参与结局比较；失联与确证未入职的区分只在成熟批次内做。
        """
        meta = self.round(round_id)
        maturity = meta.maturity_date
        for exposure in self.exposures_of(round_id):
            outcome = self.outcome(round_id, exposure.person)
            if outcome is None or parse_date(outcome.received) > maturity:
                return False
        return True

    def known_days(self, later: str, earlier: str) -> int:
        return days_between(later, earlier)


def load_snapshot(data_path: str | Path, decision_at: str | None = None) -> Snapshot:
    source = json.loads(Path(data_path).read_text(encoding="utf-8"))
    return Snapshot(source, decision_at=decision_at)
