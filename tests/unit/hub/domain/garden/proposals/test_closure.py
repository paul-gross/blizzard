"""``GardenProposalClosureService``: a blank pass reason is
refused, an already-closed proposal refuses either verb naming it, and a declining
accept records `declined` without touching ``WorkItemEditService``. The
accept-with-mint path is component-tested against a real store instead
(``tests/component/hub/store/internal/test_garden_proposal_closure_store.py``, ``…_api.py``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.garden_proposals import (
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
    GardenProposalOrigin,
)
from blizzard.hub.domain.garden.findings.model import Finding
from blizzard.hub.domain.garden.proposals.closure import (
    GardenProposalClosure,
    GardenProposalClosureService,
    GardenProposalMintFieldWithoutMint,
    GardenProposalPassReasonRequired,
    IWriteGardenProposalClosureRepository,
    compose_minted_body,
)
from blizzard.hub.domain.garden.proposals.model import GardenProposal, GardenProposalAlreadyClosed, garden_proposal_lock
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.domain.urgency.model import Urgency, UrgencyUnknown
from blizzard.hub.domain.urgency.services import UrgencyReading
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from tests.support.fake_transactions import FakeTransactions

_PROJECT = "proj_test"


_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _proposal(proposal_id: str = "gprop_1") -> GardenProposal:
    return GardenProposal(
        project_id=_PROJECT,
        proposal_id=proposal_id,
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        created_at=_T0,
        findings=["fin_1"],
    )


def _finding(finding_id: str, *, class_: str = "c", locus: str = "l", state: str = "live") -> Finding:
    return Finding(
        project_id=_PROJECT,
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


class _UntouchedUrgencies:
    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not resolve an urgency ({name!r})")


class _UnknownUrgencies:
    """Knows no urgency — every stated key is refused, recording the keys it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def live(self, field: str, key: str) -> Urgency:
        self.asked.append(key)
        raise UrgencyUnknown(field, key)


class _UntouchedWorkItemEditService:
    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _no_mint_items() -> WorkItemEditService:
    return cast(WorkItemEditService, _UntouchedWorkItemEditService())


@dataclass
class _FakeGardenProposalClosureRepo:
    closure: GardenProposalClosure | None = None
    passed: list[tuple[str, str, str, datetime]] = field(default_factory=list[tuple[str, str, str, datetime]])
    declined: list[tuple[str, str | None, str, datetime]] = field(
        default_factory=list[tuple[str, str | None, str, datetime]]
    )

    def get(self, proposal_id: str, *, transaction: Transaction | None = None) -> GardenProposalClosure | None:
        return self.closure

    def record_pass(
        self, proposal_id: str, *, reason: str, closed_by: str, at: datetime, transaction: LockedTransaction
    ) -> bool:
        if self.closure is not None:
            return False
        self.passed.append((proposal_id, reason, closed_by, at))
        return True

    def record_accept_decline(
        self, proposal_id: str, *, reason: str | None, closed_by: str, at: datetime, transaction: LockedTransaction
    ) -> bool:
        if self.closure is not None:
            return False
        self.declined.append((proposal_id, reason, closed_by, at))
        return True


def _as_write_repo(repo: _FakeGardenProposalClosureRepo) -> IWriteGardenProposalClosureRepository:
    return cast(IWriteGardenProposalClosureRepository, repo)


def _service(
    repo: _FakeGardenProposalClosureRepo,
    *,
    items: WorkItemEditService | None = None,
    transactions: FakeTransactions | None = None,
    urgencies: object | None = None,
) -> GardenProposalClosureService:
    return GardenProposalClosureService(
        closures=_as_write_repo(repo),
        items=items if items is not None else _no_mint_items(),
        default_graph=_untouched_default_graph,
        clock=FixedClock(instant=_T0),
        transactions=transactions if transactions is not None else FakeTransactions(),
        urgencies=cast(UrgencyReading, urgencies if urgencies is not None else _UntouchedUrgencies()),
    )


def _untouched_default_graph() -> Graph:
    raise NotImplementedError("should not resolve the default graph")


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
        service.accept(_proposal(), reason=None, by="u1", body=None, mint=False, findings=[])

    assert excinfo.value.closure == existing
    assert repo.declined == []


def test_accept_declining_to_mint_records_declined_and_touches_no_item_service() -> None:
    repo = _FakeGardenProposalClosureRepo()
    service = _service(repo)

    accepted = service.accept(_proposal(), reason="handled by hand", by="u1", body=None, mint=False, findings=[])

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


# --- compose_minted_body -------------------------------


def test_compose_wraps_the_given_body_with_one_findings_bullet() -> None:
    body = compose_minted_body("the case", [_finding("fin_1", class_="stale-docstring", locus="a.py:1")])

    assert body.startswith("the case\n\n## Related findings\n\n")
    assert "- `fin_1` — stale-docstring — a.py:1 — live" in body
    assert "blizzard runner finding list" in body
    assert "blizzard runner finding get <finding-id>" in body


def test_compose_carries_one_bullet_per_finding_in_order() -> None:
    findings = [
        _finding("fin_1", class_="c1", locus="a.py:1", state="live"),
        _finding("fin_2", class_="c2", locus="b.py:2", state="resolved"),
    ]

    body = compose_minted_body("the case", findings)

    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert bullets == [
        "- `fin_1` — c1 — a.py:1 — live",
        "- `fin_2` — c2 — b.py:2 — resolved",
    ]


def test_compose_wraps_the_proposals_own_body_when_none_is_given() -> None:
    """`accept()` passes ``body`` if given, else ``proposal.body`` — either way,
    ``compose_minted_body`` only ever sees the resolved string, never `None`."""
    body = compose_minted_body(_proposal().body, [_finding("fin_1")])

    assert body.startswith("the case\n\n## Related findings\n\n")


def test_compose_wraps_a_caller_supplied_override_body() -> None:
    body = compose_minted_body("a hand-drafted body", [_finding("fin_1")])

    assert body.startswith("a hand-drafted body\n\n## Related findings\n\n")


def test_compose_returns_the_body_unchanged_when_findings_is_empty() -> None:
    """A proposal citing no findings, or one whose cited ids have all since stopped
    resolving, mints a bare item — no section, no preamble."""
    body = compose_minted_body("the case", [])

    assert body == "the case"


def test_pass_and_a_declining_accept_each_declare_the_proposal_key() -> None:
    transactions = FakeTransactions()
    _service(_FakeGardenProposalClosureRepo(), transactions=transactions).pass_(_proposal(), reason="no", by="u1")
    _service(_FakeGardenProposalClosureRepo(), transactions=transactions).accept(
        _proposal(), reason=None, by="u1", body=None, mint=False, findings=[]
    )

    key = garden_proposal_lock(_proposal().proposal_id)
    assert transactions.locked_keys == [[key], [key]]


@dataclass
class _LockedOnlyClosureRepo(_FakeGardenProposalClosureRepo):
    """A closure only the re-read under the unit's lock sees — what a racing accept leaves between the early read
    and the lock."""

    minted: list[str] = field(default_factory=list[str])

    def get(self, proposal_id: str, *, transaction: Transaction | None = None) -> GardenProposalClosure | None:
        return self.closure if transaction is not None else None

    def record_accept_mint(self, proposal_id: str, **_: Any) -> bool:
        self.minted.append(proposal_id)
        return True


class _MintingItems:
    def __init__(self) -> None:
        self.minted: list[object] = []

    def draft_mint(self, source: str, **_: Any) -> object:
        class _Drafted:
            pointer = "hub:1"
            at = _T0

            def lock_keys(self) -> list[object]:
                return []

        return _Drafted()

    def mint_drafted(self, drafted: object, **_: Any) -> object:
        self.minted.append(drafted)
        raise AssertionError("a closed proposal must mint nothing")


def test_a_minting_accept_decides_on_the_closure_read_under_its_lock() -> None:
    existing = GardenProposalClosure(
        proposal_id="gprop_1",
        closure=GardenProposalClosureKind.ACCEPTED,
        reason=None,
        closed_by="u0",
        closed_at=_T0,
        item_outcome=GardenProposalItemOutcome.MINTED,
        source="hub",
        ref="wi_0",
    )
    repo = _LockedOnlyClosureRepo(closure=existing)
    items = _MintingItems()
    graph = cast(Graph, object())
    service = GardenProposalClosureService(
        closures=_as_write_repo(repo),
        items=cast(WorkItemEditService, items),
        default_graph=lambda: graph,
        clock=FixedClock(instant=_T0),
        transactions=FakeTransactions(),
        urgencies=cast(UrgencyReading, _UntouchedUrgencies()),
    )

    with pytest.raises(GardenProposalAlreadyClosed) as excinfo:
        service.accept(_proposal(), reason=None, by="u1", body=None, mint=True, findings=[])

    assert excinfo.value.closure == existing
    assert repo.minted == []
    assert items.minted == []


_CLOSED = GardenProposalClosure(
    proposal_id="gprop_1",
    closure=GardenProposalClosureKind.PASSED,
    reason="no",
    closed_by="u0",
    closed_at=_T0,
    item_outcome=None,
    source=None,
    ref=None,
)


@pytest.mark.parametrize("mint", [True, False])
def test_a_closed_proposal_is_refused_closed_before_its_stated_urgency_is_resolved(mint: bool) -> None:
    service = _service(_FakeGardenProposalClosureRepo(closure=_CLOSED), urgencies=_UntouchedUrgencies())

    with pytest.raises(GardenProposalAlreadyClosed):
        service.accept(_proposal(), reason=None, by="u1", body=None, mint=mint, findings=[], urgency="no-such")


def test_a_declining_accept_stating_an_urgency_is_refused_without_resolving_it() -> None:
    repo = _FakeGardenProposalClosureRepo()
    service = _service(repo, urgencies=_UntouchedUrgencies())

    with pytest.raises(GardenProposalMintFieldWithoutMint) as excinfo:
        service.accept(_proposal(), reason=None, by="u1", body=None, mint=False, findings=[], urgency="no-such")

    assert excinfo.value.field == "urgency"
    assert repo.declined == []


def test_an_open_minting_accept_resolves_its_stated_urgency_before_drafting() -> None:
    urgencies = _UnknownUrgencies()
    items = _MintingItems()
    service = GardenProposalClosureService(
        closures=_as_write_repo(_FakeGardenProposalClosureRepo()),
        items=cast(WorkItemEditService, items),
        default_graph=_untouched_default_graph,
        clock=FixedClock(instant=_T0),
        transactions=FakeTransactions(),
        urgencies=cast(UrgencyReading, urgencies),
    )

    with pytest.raises(UrgencyUnknown):
        service.accept(_proposal(), reason=None, by="u1", body=None, mint=True, findings=[], urgency="no-such")

    assert urgencies.asked == ["no-such"]
    assert items.minted == []
