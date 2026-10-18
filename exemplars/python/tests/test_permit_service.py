"""``PermitService`` orchestration over an in-memory repository — the rules themselves are pinned by value elsewhere."""

# exemplar: bzh:narrow-seams — a hand-written fake proved against its port by a conformance sentinel.

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.projects.model import Project, ProjectId
from exemplar.foundation.permits import PermitState
from exemplar.hub.domain.permits.model import (
    ILockedPermitRead,
    IWritePermitRepository,
    Permit,
    PermitAlreadyRequested,
    PermitFact,
    PermitFactKind,
    PermitPage,
    PermitVerbRefused,
)
from exemplar.hub.domain.permits.service import PermitService

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_AT = datetime(2026, 2, 1, tzinfo=UTC)


def _project(slug: str) -> Project:
    return Project(
        project_id=ProjectId(f"proj_{slug}"),
        slug=slug,
        name=slug.title(),
        description="",
        color=ProjectColor.TEAL,
        revision=1,
        created_at=_T0,
    )


_ALPHA = _project("alpha")
_BETA = _project("beta")
_REQUESTED = Permit.request("pm_a", project_id=_ALPHA.project_id, holder="ada", capability="deploy", by="op", at=_T0)


@dataclass
class _FakeLockedPermitRead:
    rows: dict[str, Permit]
    permit_id: str

    def permit(self) -> Permit:
        return self.rows[self.permit_id]


@dataclass
class _FakePermitRepository:
    rows: dict[str, Permit] = field(default_factory=dict)
    facts: list[PermitFact] = field(default_factory=list)

    def get(self, permit_id: str) -> Permit | None:
        return self.rows.get(permit_id)

    def get_many(self, permit_ids: Sequence[str]) -> dict[str, Permit]:
        return {i: self.rows[i] for i in permit_ids if i in self.rows}

    def list_page(self, *, project: ProjectId | None, holder: str | None, limit: int, cursor: str | None) -> PermitPage:
        newest_first = sorted(self.rows.values(), key=lambda p: (p.requested_at, p.permit_id), reverse=True)
        matching = [
            p
            for p in newest_first
            if (project is None or p.project_id == project) and (holder is None or p.holder == holder)
        ]
        start = next((i + 1 for i, p in enumerate(matching) if p.permit_id == cursor), 0) if cursor else 0
        window = matching[start : start + limit]
        more = start + limit < len(matching)
        return PermitPage(permits=tuple(window), next_cursor=window[-1].permit_id if more else None)

    def create(self, permit: Permit) -> None:
        key = (permit.project_id, permit.holder, permit.capability)
        if any((p.project_id, p.holder, p.capability) == key for p in self.rows.values()):
            raise PermitAlreadyRequested(permit.holder, capability=permit.capability)
        self.rows[permit.permit_id] = permit

    def locked(self, permit_id: str) -> AbstractContextManager[ILockedPermitRead]:
        return self._locked(permit_id)

    @contextmanager
    def _locked(self, permit_id: str) -> Iterator[ILockedPermitRead]:
        yield _FakeLockedPermitRead(self.rows, permit_id)

    def record_fact_locked(self, held: ILockedPermitRead, fact: PermitFact) -> None:
        self.facts.append(fact)
        self.rows[fact.permit_id] = replace(self.rows[fact.permit_id], latest=fact)


def _conforms_fake_permit_repository(x: _FakePermitRepository) -> IWritePermitRepository:
    return x


def _conforms_fake_locked_permit_read(x: _FakeLockedPermitRead) -> ILockedPermitRead:
    return x


def _service(permits: _FakePermitRepository) -> PermitService:
    return PermitService(permits=permits, clock=FixedClock(instant=_AT))


def test_request_creates_the_permit_in_its_project_at_the_clock_reading() -> None:
    permits = _FakePermitRepository()

    permit = _service(permits).request(project=_ALPHA, holder="ada", capability="deploy", by="op")

    assert (permit.project_id, permit.requested_at) == (_ALPHA.project_id, _AT)
    assert permit.permit_id.startswith("pm_")
    assert permits.rows == {permit.permit_id: permit}


def test_a_repeated_request_surfaces_the_ports_refusal_and_another_project_is_its_own() -> None:
    permits = _FakePermitRepository()
    service = _service(permits)
    service.request(project=_ALPHA, holder="ada", capability="deploy", by="op")

    with pytest.raises(PermitAlreadyRequested):
        service.request(project=_ALPHA, holder="ada", capability="deploy", by="op")
    service.request(project=_BETA, holder="ada", capability="deploy", by="op")

    assert len(permits.rows) == 2


def test_grant_records_the_models_fact_at_the_clock_reading_and_returns_it_as_latest() -> None:
    permits = _FakePermitRepository(rows={"pm_a": _REQUESTED})

    granted = _service(permits).grant(_REQUESTED, by="op")

    assert permits.facts == [PermitFact("pm_a", PermitFactKind.GRANTED, at=_AT, by="op")]
    assert granted.latest == permits.facts[0]
    assert granted.state() is PermitState.GRANTED


def test_revoke_records_the_note_the_model_decided() -> None:
    permits = _FakePermitRepository(rows={"pm_a": _REQUESTED})

    revoked = _service(permits).revoke(_REQUESTED, note="  leaked ", by="op")

    assert permits.facts[0].note == "leaked"
    assert revoked.state() is PermitState.REVOKED


def test_a_refused_verb_records_nothing() -> None:
    permits = _FakePermitRepository(rows={"pm_a": _REQUESTED})

    with pytest.raises(PermitVerbRefused):
        _service(permits).resume(_REQUESTED, by="op")

    assert permits.facts == []


def test_a_verb_decides_on_the_locked_reread_not_the_callers_copy() -> None:
    permits = _FakePermitRepository(rows={"pm_a": _REQUESTED})
    service = _service(permits)
    service.revoke(_REQUESTED, note="leaked", by="other")

    with pytest.raises(PermitVerbRefused) as refused:
        service.grant(_REQUESTED, by="op")

    assert refused.value.state is PermitState.REVOKED
    assert len(permits.facts) == 1
