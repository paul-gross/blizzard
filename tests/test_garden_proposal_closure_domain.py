"""``GardenProposalClosureService`` (unit tier, blizzard#395): a blank pass reason is
refused, an already-closed proposal refuses either verb naming it, and a declining
accept records `declined` without touching ``WorkItemEditService``. The
accept-with-mint path is component-tested against a real store instead
(``tests/test_garden_proposal_closure_store.py``, ``…_api.py``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.garden_proposal_closure import (
    GardenProposalAlreadyClosed,
    GardenProposalClosure,
    GardenProposalClosureKind,
    GardenProposalClosureService,
    GardenProposalItemOutcome,
    GardenProposalPassReasonRequired,
    IWriteGardenProposalClosureRepository,
)
from blizzard.hub.domain.garden_proposals import GardenProposal
from blizzard.hub.domain.work_items import WorkItemEditService

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _proposal(proposal_id: str = "gprop_1") -> GardenProposal:
    return GardenProposal(
        proposal_id=proposal_id,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        created_at=_T0,
        findings=["fin_1"],
    )


class _UntouchedWorkItemEditService:
    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _no_mint_items() -> WorkItemEditService:
    return cast(WorkItemEditService, _UntouchedWorkItemEditService())


@dataclass
class _FakeGardenProposalClosureRepo:
    closure: GardenProposalClosure | None = None
    passed: list[tuple[str, str, str, datetime]] = field(default_factory=list)
    declined: list[tuple[str, str | None, str, datetime]] = field(default_factory=list)

    def get(self, proposal_id: str) -> GardenProposalClosure | None:
        return self.closure

    def record_pass(self, proposal_id: str, *, reason: str, closed_by: str, at: datetime) -> bool:
        if self.closure is not None:
            return False
        self.passed.append((proposal_id, reason, closed_by, at))
        return True

    def record_accept_decline(self, proposal_id: str, *, reason: str | None, closed_by: str, at: datetime) -> bool:
        if self.closure is not None:
            return False
        self.declined.append((proposal_id, reason, closed_by, at))
        return True


def _as_write_repo(repo: _FakeGardenProposalClosureRepo) -> IWriteGardenProposalClosureRepository:
    return cast(IWriteGardenProposalClosureRepository, repo)


def _service(
    repo: _FakeGardenProposalClosureRepo, *, items: WorkItemEditService | None = None
) -> GardenProposalClosureService:
    return GardenProposalClosureService(
        closures=_as_write_repo(repo),
        items=items if items is not None else _no_mint_items(),
        clock=FixedClock(instant=_T0),
    )


def test_pass_rejects_a_blank_reason() -> None:
    repo = _FakeGardenProposalClosureRepo()
    service = _service(repo)

    with pytest.raises(GardenProposalPassReasonRequired):
        service.pass_(_proposal(), reason="   ", by="u1")

    assert repo.passed == []


def test_pass_strips_and_records_the_reason() -> None:
    repo = _FakeGardenProposalClosureRepo()
    service = _service(repo)

    closure = service.pass_(_proposal(), reason="  not worth it  ", by="u1")

    assert repo.passed == [("gprop_1", "not worth it", "u1", _T0)]
    assert closure == GardenProposalClosure(
        proposal_id="gprop_1",
        closure=GardenProposalClosureKind.PASSED,
        reason="not worth it",
        closed_by="u1",
        closed_at=_T0,
        item_outcome=None,
        source=None,
        ref=None,
    )


def test_pass_on_an_already_closed_proposal_is_refused_naming_the_existing_closure() -> None:
    existing = GardenProposalClosure(
        proposal_id="gprop_1",
        closure=GardenProposalClosureKind.PASSED,
        reason="already passed",
        closed_by="u0",
        closed_at=_T0,
        item_outcome=None,
        source=None,
        ref=None,
    )
    repo = _FakeGardenProposalClosureRepo(closure=existing)
    service = _service(repo)

    with pytest.raises(GardenProposalAlreadyClosed) as excinfo:
        service.pass_(_proposal(), reason="reconsidered", by="u1")

    assert excinfo.value.closure == existing
    assert repo.passed == []


def test_accept_on_an_already_closed_proposal_is_refused_naming_the_existing_closure() -> None:
    existing = GardenProposalClosure(
        proposal_id="gprop_1",
        closure=GardenProposalClosureKind.ACCEPTED,
        reason=None,
        closed_by="u0",
        closed_at=_T0,
        item_outcome=GardenProposalItemOutcome.DECLINED,
        source=None,
        ref=None,
    )
    repo = _FakeGardenProposalClosureRepo(closure=existing)
    service = _service(repo)

    with pytest.raises(GardenProposalAlreadyClosed) as excinfo:
        service.accept(_proposal(), reason=None, by="u1", body=None, mint=False, graph=None)

    assert excinfo.value.closure == existing
    assert repo.declined == []


def test_accept_declining_to_mint_records_declined_and_touches_no_item_service() -> None:
    repo = _FakeGardenProposalClosureRepo()
    service = _service(repo)

    accepted = service.accept(_proposal(), reason="handled by hand", by="u1", body=None, mint=False, graph=None)

    assert repo.declined == [("gprop_1", "handled by hand", "u1", _T0)]
    assert accepted.chunk_id is None
    assert accepted.closure == GardenProposalClosure(
        proposal_id="gprop_1",
        closure=GardenProposalClosureKind.ACCEPTED,
        reason="handled by hand",
        closed_by="u1",
        closed_at=_T0,
        item_outcome=GardenProposalItemOutcome.DECLINED,
        source=None,
        ref=None,
    )
