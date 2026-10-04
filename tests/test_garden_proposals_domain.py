"""``GardenProposalAuthoring`` and ``RoutineGardenProposalReader`` (unit tier):
operator create, edit, attach, and detach over a fake repository — every refusal is
decided in the domain, and a clean call mints a `gprop_` id or delegates with the
clock's instant (``bzh:domain-core``, the ``tests/test_scope_domain.py`` shape).
``RoutineGardenProposalReader`` filters a routine's proposals by closure state."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.garden_proposals import GardenProposalClosureKind, GardenProposalOrigin
from blizzard.hub.domain.garden.findings.model import Finding
from blizzard.hub.domain.garden.proposals.closure import GardenProposalClosure
from blizzard.hub.domain.garden.proposals.model import (
    DuplicateProposalFindingError,
    GardenProposal,
    GardenProposalAlreadyClosed,
    GardenProposalAuthoring,
    GardenProposalBlankFieldError,
    GardenProposalEdit,
    GardenProposalEmptyEditError,
    GardenProposalFindingAlreadyLinkedError,
    GardenProposalFindingExitedError,
    GardenProposalFindingNotLinkedError,
    IWriteGardenProposalRepository,
    RoutineGardenProposalReader,
    RoutineProposalState,
)
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.kernel.unset import UnsetType

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
    created: list[tuple[str, GardenProposalOrigin, str | None, str | None, str, str, str, list[str], datetime]] = field(
        default_factory=list
    )
    edited: list[tuple[str, GardenProposalEdit]] = field(default_factory=list)
    attached: list[tuple[str, list[str]]] = field(default_factory=list)
    detached: list[tuple[str, list[str]]] = field(default_factory=list)
    #: Proposal ids `edit`/`attach`/`detach` treat as already closed, mirroring the
    #: store's own closed-then-write guard returning `None`.
    closed_ids: set[str] = field(default_factory=set)

    def create(
        self,
        proposal_id: str,
        *,
        origin: GardenProposalOrigin,
        routine_name: str | None,
        created_by: str | None = None,
        class_: str,
        title: str,
        body: str,
        findings: list[str],
        at: datetime,
    ) -> GardenProposal:
        self.created.append((proposal_id, origin, routine_name, created_by, class_, title, body, findings, at))
        return GardenProposal(
            proposal_id=proposal_id,
            origin=origin,
            routine_name=routine_name,
            created_by=created_by,
            class_=class_,
            title=title,
            body=body,
            created_at=at,
            findings=findings,
        )

    def edit(self, proposal_id: str, edit: GardenProposalEdit) -> GardenProposal | None:
        if proposal_id in self.closed_ids:
            return None
        self.edited.append((proposal_id, edit))
        title = "t" if isinstance(edit.title, UnsetType) else edit.title
        class_ = "c" if isinstance(edit.class_, UnsetType) else edit.class_
        body = "b" if isinstance(edit.body, UnsetType) else edit.body
        return GardenProposal(
            proposal_id=proposal_id,
            origin=GardenProposalOrigin.OPERATOR,
            routine_name=None,
            created_by="operator",
            class_=class_,
            title=title,
            body=body,
            created_at=_T0,
            findings=[],
        )

    def attach(self, proposal_id: str, finding_ids: list[str]) -> GardenProposal | None:
        if proposal_id in self.closed_ids:
            return None
        self.attached.append((proposal_id, list(finding_ids)))
        return GardenProposal(
            proposal_id=proposal_id,
            origin=GardenProposalOrigin.OPERATOR,
            routine_name=None,
            created_by="operator",
            class_="c",
            title="t",
            body="b",
            created_at=_T0,
            findings=list(finding_ids),
        )

    def detach(self, proposal_id: str, finding_ids: list[str]) -> GardenProposal | None:
        if proposal_id in self.closed_ids:
            return None
        self.detached.append((proposal_id, list(finding_ids)))
        return GardenProposal(
            proposal_id=proposal_id,
            origin=GardenProposalOrigin.OPERATOR,
            routine_name=None,
            created_by="operator",
            class_="c",
            title="t",
            body="b",
            created_at=_T0,
            findings=[],
        )

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_repo(repo: _FakeGardenProposalRepo) -> IWriteGardenProposalRepository:
    return cast(IWriteGardenProposalRepository, repo)


def _routine(name: str = "nightly") -> Routine:
    return Routine(routine_id="routine_1", name=name, graph_name="g", default_scope_slug="runner", created_at=_T0)


def test_create_operator_mints_with_operator_origin_no_routine_and_the_callers_id() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )

    proposal = authoring.create_operator(
        created_by="operator", routine=None, class_="c", title="t", body="b", findings=[_finding("fin_1")]
    )

    assert proposal.proposal_id.startswith("gprop_")
    assert proposal.origin is GardenProposalOrigin.OPERATOR
    assert proposal.created_by == "operator"
    assert proposal.routine_name is None
    assert repo.created == [
        (proposal.proposal_id, GardenProposalOrigin.OPERATOR, None, "operator", "c", "t", "b", ["fin_1"], _T0)
    ]


def test_create_operator_names_the_resolved_routine() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )

    proposal = authoring.create_operator(
        created_by="operator", routine=_routine("nightly"), class_="c", title="t", body="b", findings=[]
    )

    assert proposal.routine_name == "nightly"


def test_create_operator_rejects_an_exited_finding() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    gone = replace(_finding("fin_1"), live=False, state="wont-fix")

    with pytest.raises(GardenProposalFindingExitedError):
        authoring.create_operator(created_by="operator", routine=None, class_="c", title="t", body="b", findings=[gone])

    assert repo.created == []


def test_create_operator_rejects_the_same_finding_named_twice() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )

    with pytest.raises(DuplicateProposalFindingError):
        authoring.create_operator(
            created_by="operator",
            routine=None,
            class_="c",
            title="t",
            body="b",
            findings=[_finding("fin_1"), _finding("fin_1")],
        )

    assert repo.created == []


def test_create_operator_rejects_a_blank_class() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )

    with pytest.raises(GardenProposalBlankFieldError):
        authoring.create_operator(created_by="operator", routine=None, class_=" ", title="t", body="b", findings=[])

    assert repo.created == []


def _proposal(proposal_id: str) -> GardenProposal:
    return GardenProposal(
        proposal_id=proposal_id,
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="t",
        body="b",
        created_at=_T0,
        findings=["fin_1"],
    )


def test_edit_replaces_only_the_given_fields() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    authoring.edit(proposal, GardenProposalEdit(title="new title"))

    assert repo.edited == [("gprop_1", GardenProposalEdit(title="new title"))]


def test_edit_a_closed_proposal_raises_already_closed_before_reaching_the_store() -> None:
    """The closures pre-check refuses before the store is ever touched."""
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalAlreadyClosed):
        authoring.edit(proposal, GardenProposalEdit(title="new title"))

    assert repo.edited == []


def test_edit_closed_first_wins_over_a_blank_field() -> None:
    """A close racing in ahead of the check must be reported as closed, not as the
    unrelated blank-field problem the same call also carries."""
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalAlreadyClosed):
        authoring.edit(proposal, GardenProposalEdit(title="   "))

    assert repo.edited == []


def test_edit_a_closed_proposal_the_store_alone_detects_still_raises_already_closed() -> None:
    """The closures pre-check can miss a close that lands after it ran; the store's own
    row-locked check still catches it and the domain still raises."""
    repo = _FakeGardenProposalRepo(closed_ids={"gprop_1"})
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed_late={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalAlreadyClosed):
        authoring.edit(proposal, GardenProposalEdit(title="new title"))

    assert repo.edited == []


def test_edit_rejects_a_blank_title() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalBlankFieldError):
        authoring.edit(proposal, GardenProposalEdit(title="   "))

    assert repo.edited == []


def test_edit_naming_no_field_raises_empty_edit() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalEmptyEditError):
        authoring.edit(proposal, GardenProposalEdit())

    assert repo.edited == []


def test_attach_links_a_live_unlinked_finding() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")  # already links fin_1

    authoring.attach(proposal, [_finding("fin_2")])

    assert repo.attached == [("gprop_1", ["fin_2"])]


def test_attach_rejects_a_finding_already_linked_to_this_proposal() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")  # already links fin_1

    with pytest.raises(GardenProposalFindingAlreadyLinkedError):
        authoring.attach(proposal, [_finding("fin_1")])

    assert repo.attached == []


def test_attach_rejects_an_exited_finding() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")
    gone = replace(_finding("fin_2"), live=False, state="wont-fix")

    with pytest.raises(GardenProposalFindingExitedError):
        authoring.attach(proposal, [gone])

    assert repo.attached == []


def test_attach_rejects_the_same_finding_named_twice() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(DuplicateProposalFindingError):
        authoring.attach(proposal, [_finding("fin_2"), _finding("fin_2")])

    assert repo.attached == []


def test_attach_a_closed_proposal_raises_already_closed_before_reaching_the_store() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalAlreadyClosed):
        authoring.attach(proposal, [_finding("fin_2")])

    assert repo.attached == []


def test_attach_closed_first_wins_over_a_duplicate_finding() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalAlreadyClosed):
        authoring.attach(proposal, [_finding("fin_2"), _finding("fin_2")])

    assert repo.attached == []


def test_attach_a_closed_proposal_the_store_alone_detects_still_raises_already_closed() -> None:
    repo = _FakeGardenProposalRepo(closed_ids={"gprop_1"})
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed_late={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalAlreadyClosed):
        authoring.attach(proposal, [_finding("fin_2")])


def test_detach_unlinks_a_linked_finding() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")  # links fin_1

    authoring.detach(proposal, [_finding("fin_1")])

    assert repo.detached == [("gprop_1", ["fin_1"])]


def test_detach_rejects_a_finding_not_linked_to_this_proposal() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")  # links fin_1 only

    with pytest.raises(GardenProposalFindingNotLinkedError):
        authoring.detach(proposal, [_finding("fin_ghost")])

    assert repo.detached == []


def test_detach_rejects_the_same_finding_named_twice() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(DuplicateProposalFindingError):
        authoring.detach(proposal, [_finding("fin_1"), _finding("fin_1")])

    assert repo.detached == []


def test_detach_a_closed_proposal_raises_already_closed_before_reaching_the_store() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalAlreadyClosed):
        authoring.detach(proposal, [_finding("fin_1")])

    assert repo.detached == []


def test_detach_a_closed_proposal_the_store_alone_detects_still_raises_already_closed() -> None:
    repo = _FakeGardenProposalRepo(closed_ids={"gprop_1"})
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed_late={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalAlreadyClosed):
        authoring.detach(proposal, [_finding("fin_1")])


@dataclass
class _FakeReadGardenProposalRepo:
    by_routine: dict[str, list[GardenProposal]] = field(default_factory=dict)

    closed_ids: set[str] = field(default_factory=set)

    def list_for_routine(
        self, routine_name: str, *, state: RoutineProposalState = RoutineProposalState.ALL
    ) -> list[GardenProposal]:
        proposals = self.by_routine.get(routine_name, [])
        if state is RoutineProposalState.OPEN:
            return [p for p in proposals if p.proposal_id not in self.closed_ids]
        if state is RoutineProposalState.CLOSED:
            return [p for p in proposals if p.proposal_id in self.closed_ids]
        return proposals

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


@dataclass
class _FakeGardenProposalClosureRepo:
    closed: dict[str, GardenProposalClosure] = field(default_factory=dict)
    #: Land after the id's first read — a close racing the domain's pre-check.
    closed_late: dict[str, GardenProposalClosure] = field(default_factory=dict)

    def get(self, proposal_id: str) -> GardenProposalClosure | None:
        if proposal_id in self.closed_late and proposal_id not in self.closed:
            self.closed[proposal_id] = self.closed_late[proposal_id]
            return None
        return self.closed.get(proposal_id)

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
    proposals = _FakeReadGardenProposalRepo(
        by_routine={"nightly": [_proposal("gprop_1"), _proposal("gprop_2")]}, closed_ids={"gprop_2"}
    )
    closures = _FakeGardenProposalClosureRepo(closed={"gprop_2": _closure("gprop_2")})
    reader = RoutineGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    open_proposals = reader.list_for_routine("nightly", RoutineProposalState.OPEN)

    assert [p.proposal_id for p, _ in open_proposals] == ["gprop_1"]


def test_open_reader_returns_everything_when_none_are_closed() -> None:
    proposals = _FakeReadGardenProposalRepo(by_routine={"nightly": [_proposal("gprop_1")]})
    closures = _FakeGardenProposalClosureRepo()
    reader = RoutineGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    open_proposals = reader.list_for_routine("nightly", RoutineProposalState.OPEN)

    assert [p.proposal_id for p, _ in open_proposals] == ["gprop_1"]


def test_open_reader_is_empty_for_a_routine_with_no_proposals() -> None:
    reader = RoutineGardenProposalReader(
        proposals=cast(Any, _FakeReadGardenProposalRepo()), closures=cast(Any, _FakeGardenProposalClosureRepo())
    )

    assert reader.list_for_routine("nightly", RoutineProposalState.OPEN) == []


def test_list_for_routine_closed_returns_only_closed_proposals_with_their_closure() -> None:
    proposals = _FakeReadGardenProposalRepo(
        by_routine={"nightly": [_proposal("gprop_open"), _proposal("gprop_closed")]}, closed_ids={"gprop_closed"}
    )
    closures = _FakeGardenProposalClosureRepo(closed={"gprop_closed": _closure("gprop_closed")})
    reader = RoutineGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    rows = reader.list_for_routine("nightly", RoutineProposalState.CLOSED)

    assert [p.proposal_id for p, _ in rows] == ["gprop_closed"]
    closure = rows[0][1]
    assert closure is not None
    assert closure.reason == "not worth it"


def test_list_for_routine_all_returns_every_proposal_with_its_closure_when_one_exists() -> None:
    proposals = _FakeReadGardenProposalRepo(
        by_routine={"nightly": [_proposal("gprop_open"), _proposal("gprop_closed")]}, closed_ids={"gprop_closed"}
    )
    closures = _FakeGardenProposalClosureRepo(closed={"gprop_closed": _closure("gprop_closed")})
    reader = RoutineGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    rows = reader.list_for_routine("nightly", RoutineProposalState.ALL)

    assert [(p.proposal_id, c.reason if c else None) for p, c in rows] == [
        ("gprop_open", None),
        ("gprop_closed", "not worth it"),
    ]


def test_list_for_routine_defaults_to_open() -> None:
    proposals = _FakeReadGardenProposalRepo(
        by_routine={"nightly": [_proposal("gprop_1"), _proposal("gprop_2")]}, closed_ids={"gprop_2"}
    )
    closures = _FakeGardenProposalClosureRepo(closed={"gprop_2": _closure("gprop_2")})
    reader = RoutineGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    rows = reader.list_for_routine("nightly")

    assert [(p.proposal_id, c) for p, c in rows] == [("gprop_1", None)]


def _authoring_over(repo: _FakeGardenProposalRepo) -> GardenProposalAuthoring:
    return GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )


def test_detach_unlinks_an_exited_finding_because_exit_is_not_checked() -> None:
    repo = _FakeGardenProposalRepo()
    exited = replace(_finding("fin_1"), live=False, state="wont-fix")

    _authoring_over(repo).detach(_proposal("gprop_1"), [exited])

    assert repo.detached == [("gprop_1", ["fin_1"])]


def test_detach_unlinks_a_gone_finding() -> None:
    repo = _FakeGardenProposalRepo()
    gone = replace(_finding("fin_1"), live=False, state="gone")

    _authoring_over(repo).detach(_proposal("gprop_1"), [gone])

    assert repo.detached == [("gprop_1", ["fin_1"])]


def test_edit_strips_a_padded_class_and_body() -> None:
    repo = _FakeGardenProposalRepo()

    _authoring_over(repo).edit(_proposal("gprop_1"), GardenProposalEdit(class_="  new-class \n", body="\n new body  "))

    assert repo.edited == [("gprop_1", GardenProposalEdit(class_="new-class", body="new body"))]


def test_edit_rejects_a_blank_class() -> None:
    repo = _FakeGardenProposalRepo()

    with pytest.raises(GardenProposalBlankFieldError) as raised:
        _authoring_over(repo).edit(_proposal("gprop_1"), GardenProposalEdit(class_="  "))

    assert "class" in str(raised.value)
    assert repo.edited == []


def test_edit_rejects_a_blank_body() -> None:
    repo = _FakeGardenProposalRepo()

    with pytest.raises(GardenProposalBlankFieldError) as raised:
        _authoring_over(repo).edit(_proposal("gprop_1"), GardenProposalEdit(body="\n\t"))

    assert "body" in str(raised.value)
    assert repo.edited == []
