"""``GardenProposalAuthoring`` (unit tier, blizzard#390): create over a fake repository —
an empty ``findings`` list is refused (D7), a duplicate-naming one is refused, and a
clean non-empty one mints a `gprop_` id and delegates to the repository with the clock's
instant (``bzh:domain-core``, the ``tests/test_scope_domain.py`` shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.findings import Finding
from blizzard.hub.domain.garden_proposals import (
    DuplicateProposalFindingError,
    EmptyProposalFindingsError,
    GardenProposal,
    GardenProposalAuthoring,
    IWriteGardenProposalRepository,
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
