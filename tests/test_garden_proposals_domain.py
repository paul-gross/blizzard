"""``GardenProposalAuthoring`` and ``OpenGardenProposalReader`` (unit tier, blizzard#390):
create over a fake repository — an empty ``findings`` list is refused (D7), a
duplicate-naming one is refused, and a clean one mints a `gprop_` id and delegates with
the clock's instant (``bzh:domain-core``, the ``tests/test_scope_domain.py`` shape).
``OpenGardenProposalReader`` filters out any proposal a closure already exists for."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.findings import Finding
from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosure, GardenProposalClosureKind
from blizzard.hub.domain.garden_proposals import (
    DuplicateProposalFindingError,
    EmptyProposalFindingsError,
    GardenProposal,
    GardenProposalAuthoring,
    IWriteGardenProposalRepository,
    OpenGardenProposalReader,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _finding(finding_id: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        routine_name="nightly",
        scope_slug="runner",
        class_="stale-docstring",
        locus="src/a.py:1",
        summary="s",
        introduced=None,
        introduced_at=None,
        first_observed_at=_T0,
        live=True,
        state="live",
        note=None,
        last_seen_at=_T0,
        observed_count=0,
    )


@dataclass
class _FakeGardenProposalRepo:
    created: list[tuple[str, str, str, str, str, list[str], datetime]] = field(default_factory=list)

    def create(
        self,
        proposal_id: str,
        *,
        routine_name: str,
        class_: str,
        title: str,
        body: str,
        findings: list[str],
        at: datetime,
    ) -> GardenProposal:
        self.created.append((proposal_id, routine_name, class_, title, body, findings, at))
        return GardenProposal(
            proposal_id=proposal_id,
            routine_name=routine_name,
            class_=class_,
            title=title,
            body=body,
            created_at=at,
            findings=findings,
        )

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_repo(repo: _FakeGardenProposalRepo) -> IWriteGardenProposalRepository:
    return cast(IWriteGardenProposalRepository, repo)


def test_create_rejects_an_empty_findings_list() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(proposals=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    with pytest.raises(EmptyProposalFindingsError):
        authoring.create(routine_name="nightly", class_="fix-the-source", title="t", body="b", findings=[])

    assert repo.created == []


def test_create_mints_a_gprop_id_and_delegates_with_the_clock_instant() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(proposals=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    proposal = authoring.create(
        routine_name="nightly",
        class_="fix-the-source",
        title="t",
        body="b",
        findings=[_finding("fin_1"), _finding("fin_2")],
    )

    assert proposal.proposal_id.startswith("gprop_")
    assert repo.created == [(proposal.proposal_id, "nightly", "fix-the-source", "t", "b", ["fin_1", "fin_2"], _T0)]


def test_create_rejects_the_same_finding_named_twice() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(proposals=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    with pytest.raises(DuplicateProposalFindingError):
        authoring.create(
            routine_name="nightly",
            class_="fix-the-source",
            title="t",
            body="b",
            findings=[_finding("fin_1"), _finding("fin_1")],
        )

    assert repo.created == []


def _proposal(proposal_id: str) -> GardenProposal:
    return GardenProposal(
        proposal_id=proposal_id,
        routine_name="nightly",
        class_="fix-the-source",
        title="t",
        body="b",
        created_at=_T0,
        findings=["fin_1"],
    )


@dataclass
class _FakeReadGardenProposalRepo:
    by_routine: dict[str, list[GardenProposal]] = field(default_factory=dict)

    def list_for_routine(self, routine_name: str) -> list[GardenProposal]:
        return self.by_routine.get(routine_name, [])

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


@dataclass
class _FakeGardenProposalClosureRepo:
    closed: dict[str, GardenProposalClosure] = field(default_factory=dict)

    def get_many(self, proposal_ids: list[str]) -> dict[str, GardenProposalClosure]:
        return {pid: self.closed[pid] for pid in proposal_ids if pid in self.closed}

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _closure(proposal_id: str) -> GardenProposalClosure:
    return GardenProposalClosure(
        proposal_id=proposal_id,
        closure=GardenProposalClosureKind.PASSED,
        reason="not worth it",
        closed_by="u_1",
        closed_at=_T0,
        item_outcome=None,
        source=None,
        ref=None,
    )


def test_open_reader_excludes_a_proposal_already_carrying_a_closure() -> None:
    proposals = _FakeReadGardenProposalRepo(by_routine={"nightly": [_proposal("gprop_1"), _proposal("gprop_2")]})
    closures = _FakeGardenProposalClosureRepo(closed={"gprop_2": _closure("gprop_2")})
    reader = OpenGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    open_proposals = reader.list_open_for_routine("nightly")

    assert [p.proposal_id for p in open_proposals] == ["gprop_1"]


def test_open_reader_returns_everything_when_none_are_closed() -> None:
    proposals = _FakeReadGardenProposalRepo(by_routine={"nightly": [_proposal("gprop_1")]})
    closures = _FakeGardenProposalClosureRepo()
    reader = OpenGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    open_proposals = reader.list_open_for_routine("nightly")

    assert [p.proposal_id for p in open_proposals] == ["gprop_1"]


def test_open_reader_is_empty_for_a_routine_with_no_proposals() -> None:
    reader = OpenGardenProposalReader(
        proposals=cast(Any, _FakeReadGardenProposalRepo()), closures=cast(Any, _FakeGardenProposalClosureRepo())
    )

    assert reader.list_open_for_routine("nightly") == []
