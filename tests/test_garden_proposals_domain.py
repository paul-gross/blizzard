"""``GardenProposalAuthoring`` (unit tier, blizzard#390): create over a fake repository —
an empty ``findings`` list is refused (D7), and a non-empty one mints a `prop_` id and
delegates to the repository with the clock's instant (``bzh:domain-core``, the
``tests/test_scope_domain.py`` shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.garden_proposals import (
    EmptyProposalFindingsError,
    GardenProposal,
    GardenProposalAuthoring,
    IWriteGardenProposalRepository,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


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


def test_create_mints_a_prop_id_and_delegates_with_the_clock_instant() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(proposals=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    proposal = authoring.create(
        routine_name="nightly", class_="fix-the-source", title="t", body="b", findings=["fin_1", "fin_2"]
    )

    assert proposal.proposal_id.startswith("prop_")
    assert repo.created == [(proposal.proposal_id, "nightly", "fix-the-source", "t", "b", ["fin_1", "fin_2"], _T0)]
