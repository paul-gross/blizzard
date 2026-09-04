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
from blizzard.hub.domain.findings import Finding
from blizzard.hub.domain.garden_proposal_closure import (
    GardenProposalAlreadyClosed,
    GardenProposalClosure,
    GardenProposalClosureKind,
    GardenProposalClosureService,
    GardenProposalItemOutcome,
    GardenProposalPassReasonRequired,
    IWriteGardenProposalClosureRepository,
    _compose_minted_body,
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


def _finding(finding_id: str, *, class_: str = "c", locus: str = "l", state: str = "live") -> Finding:
    return Finding(
        finding_id=finding_id,
        routine_name="nightly",
        scope_slug="blizzard",
        class_=class_,
        locus=locus,
        summary="s",
        introduced=None,
        introduced_at=None,
        first_observed_at=_T0,
        live=state == "live",
        state=state,
        note=None,
        last_seen_at=_T0,
        observed_count=0,
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
        service.accept(_proposal(), reason=None, by="u1", body=None, mint=False, graph=None, findings=[])

    assert excinfo.value.closure == existing
    assert repo.declined == []


def test_accept_declining_to_mint_records_declined_and_touches_no_item_service() -> None:
    repo = _FakeGardenProposalClosureRepo()
    service = _service(repo)

    accepted = service.accept(
        _proposal(), reason="handled by hand", by="u1", body=None, mint=False, graph=None, findings=[]
    )

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


# --- _compose_minted_body (blizzard#397 Phase 3) -------------------------------


def test_compose_wraps_the_given_body_with_one_findings_bullet() -> None:
    body = _compose_minted_body("the case", [_finding("fin_1", class_="stale-docstring", locus="a.py:1")])

    assert body.startswith("the case\n\n## Related findings\n\n")
    assert "- `fin_1` — stale-docstring — a.py:1 — live" in body
    assert "blizzard runner finding list" in body
    assert "blizzard runner finding get <finding-id>" in body


def test_compose_carries_one_bullet_per_finding_in_order() -> None:
    findings = [
        _finding("fin_1", class_="c1", locus="a.py:1", state="live"),
        _finding("fin_2", class_="c2", locus="b.py:2", state="resolved"),
    ]

    body = _compose_minted_body("the case", findings)

    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert bullets == [
        "- `fin_1` — c1 — a.py:1 — live",
        "- `fin_2` — c2 — b.py:2 — resolved",
    ]


def test_compose_wraps_the_proposals_own_body_when_none_is_given() -> None:
    """`accept()` passes ``body`` if given, else ``proposal.body`` — either way,
    ``_compose_minted_body`` only ever sees the resolved string, never `None`."""
    body = _compose_minted_body(_proposal().body, [_finding("fin_1")])

    assert body.startswith("the case\n\n## Related findings\n\n")


def test_compose_wraps_a_caller_supplied_override_body() -> None:
    body = _compose_minted_body("a hand-drafted body", [_finding("fin_1")])

    assert body.startswith("a hand-drafted body\n\n## Related findings\n\n")
