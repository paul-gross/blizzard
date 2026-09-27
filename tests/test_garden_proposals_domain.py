"""``GardenProposalAuthoring`` and ``OpenGardenProposalReader`` (unit tier, blizzard#390):
create over a fake repository — an empty ``findings`` list is accepted, a
duplicate-naming one is refused, and a clean one mints a `gprop_` id and delegates with
the clock's instant (``bzh:domain-core``, the ``tests/test_scope_domain.py`` shape).
``OpenGardenProposalReader`` filters out any proposal a closure already exists for."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.findings import Finding
from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosure, GardenProposalClosureKind
from blizzard.hub.domain.garden_proposals import (
    DuplicateProposalFindingError,
    GardenProposal,
    GardenProposalAuthoring,
    GardenProposalBlankFieldError,
    GardenProposalEdit,
    GardenProposalEmptyEditError,
    GardenProposalFindingAlreadyLinkedError,
    GardenProposalFindingNotLinkedError,
    GardenProposalFindingNotLiveError,
    GardenProposalNotOpen,
    GardenProposalOrigin,
    IWriteGardenProposalRepository,
    OpenGardenProposalReader,
    RoutineProposalState,
)
from blizzard.hub.domain.routines import Routine

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
    edited: list[tuple[str, str, str, str]] = field(default_factory=list)
    attached: list[tuple[str, list[str]]] = field(default_factory=list)
    detached: list[tuple[str, list[str]]] = field(default_factory=list)
    #: Proposal ids `edit`/`attach`/`detach` treat as already closed, mirroring the
    #: store's own closed-then-write guard returning `None` (D3).
    closed_ids: set[str] = field(default_factory=set)

    def create(
        self,
        proposal_id: str,
        *,
        origin: GardenProposalOrigin = GardenProposalOrigin.ROUTINE_RUN,
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

    def edit(self, proposal_id: str, *, title: str, class_: str, body: str) -> GardenProposal | None:
        if proposal_id in self.closed_ids:
            return None
        self.edited.append((proposal_id, title, class_, body))
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


def test_create_accepts_an_empty_findings_list() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )

    proposal = authoring.create(routine_name="nightly", class_="fix-the-source", title="t", body="b", findings=[])

    assert proposal.findings == []
    assert repo.created == [
        (proposal.proposal_id, GardenProposalOrigin.ROUTINE_RUN, "nightly", None, "fix-the-source", "t", "b", [], _T0)
    ]


def test_create_mints_a_gprop_id_and_delegates_with_the_clock_instant() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )

    proposal = authoring.create(
        routine_name="nightly",
        class_="fix-the-source",
        title="t",
        body="b",
        findings=[_finding("fin_1"), _finding("fin_2")],
    )

    assert proposal.proposal_id.startswith("gprop_")
    assert repo.created == [
        (
            proposal.proposal_id,
            GardenProposalOrigin.ROUTINE_RUN,
            "nightly",
            None,
            "fix-the-source",
            "t",
            "b",
            ["fin_1", "fin_2"],
            _T0,
        )
    ]


def test_create_rejects_the_same_finding_named_twice() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )

    with pytest.raises(DuplicateProposalFindingError):
        authoring.create(
            routine_name="nightly",
            class_="fix-the-source",
            title="t",
            body="b",
            findings=[_finding("fin_1"), _finding("fin_1")],
        )

    assert repo.created == []


def test_create_accepts_a_blank_body_matching_the_pre_operator_mint_path() -> None:
    """`create` is the routine-run mint delivery already used before operator
    authorship existed — it never validated blank fields, and still doesn't."""
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )

    proposal = authoring.create(routine_name="nightly", class_="fix-the-source", title="t", body="", findings=[])

    assert proposal.body == ""


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


def test_create_operator_rejects_a_non_live_finding() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    gone = replace(_finding("fin_1"), live=False)

    with pytest.raises(GardenProposalFindingNotLiveError):
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

    assert repo.edited == [("gprop_1", "new title", proposal.class_, proposal.body)]


def test_edit_a_closed_proposal_raises_not_open_before_reaching_the_store() -> None:
    """The closures pre-check refuses before the store is ever touched."""
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalNotOpen):
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

    with pytest.raises(GardenProposalNotOpen):
        authoring.edit(proposal, GardenProposalEdit(title="   "))

    assert repo.edited == []


def test_edit_a_closed_proposal_the_store_alone_detects_still_raises_not_open() -> None:
    """The closures pre-check can miss a close that lands after it ran; the store's own
    row-locked check still catches it and the domain still raises."""
    repo = _FakeGardenProposalRepo(closed_ids={"gprop_1"})
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalNotOpen):
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


def test_attach_rejects_a_non_live_finding() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")
    gone = replace(_finding("fin_2"), live=False)

    with pytest.raises(GardenProposalFindingNotLiveError):
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


def test_attach_a_closed_proposal_raises_not_open_before_reaching_the_store() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalNotOpen):
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

    with pytest.raises(GardenProposalNotOpen):
        authoring.attach(proposal, [_finding("fin_2"), _finding("fin_2")])

    assert repo.attached == []


def test_attach_a_closed_proposal_the_store_alone_detects_still_raises_not_open() -> None:
    repo = _FakeGardenProposalRepo(closed_ids={"gprop_1"})
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalNotOpen):
        authoring.attach(proposal, [_finding("fin_2")])


def test_detach_unlinks_a_linked_finding() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")  # links fin_1

    authoring.detach(proposal, ["fin_1"])

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
        authoring.detach(proposal, ["fin_ghost"])

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
        authoring.detach(proposal, ["fin_1", "fin_1"])

    assert repo.detached == []


def test_detach_a_closed_proposal_raises_not_open_before_reaching_the_store() -> None:
    repo = _FakeGardenProposalRepo()
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo(closed={"gprop_1": _closure("gprop_1")})),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalNotOpen):
        authoring.detach(proposal, ["fin_1"])

    assert repo.detached == []


def test_detach_a_closed_proposal_the_store_alone_detects_still_raises_not_open() -> None:
    repo = _FakeGardenProposalRepo(closed_ids={"gprop_1"})
    authoring = GardenProposalAuthoring(
        proposals=_as_write_repo(repo),
        closures=cast(Any, _FakeGardenProposalClosureRepo()),
        clock=FixedClock(instant=_T0),
    )
    proposal = _proposal("gprop_1")

    with pytest.raises(GardenProposalNotOpen):
        authoring.detach(proposal, ["fin_1"])


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

    def get(self, proposal_id: str) -> GardenProposalClosure | None:
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


def test_list_for_routine_closed_returns_only_closed_proposals_with_their_closure() -> None:
    proposals = _FakeReadGardenProposalRepo(
        by_routine={"nightly": [_proposal("gprop_open"), _proposal("gprop_closed")]}
    )
    closures = _FakeGardenProposalClosureRepo(closed={"gprop_closed": _closure("gprop_closed")})
    reader = OpenGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    rows = reader.list_for_routine("nightly", RoutineProposalState.CLOSED)

    assert [p.proposal_id for p, _ in rows] == ["gprop_closed"]
    closure = rows[0][1]
    assert closure is not None
    assert closure.reason == "not worth it"


def test_list_for_routine_all_returns_every_proposal_with_its_closure_when_one_exists() -> None:
    proposals = _FakeReadGardenProposalRepo(
        by_routine={"nightly": [_proposal("gprop_open"), _proposal("gprop_closed")]}
    )
    closures = _FakeGardenProposalClosureRepo(closed={"gprop_closed": _closure("gprop_closed")})
    reader = OpenGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    rows = reader.list_for_routine("nightly", RoutineProposalState.ALL)

    assert [(p.proposal_id, c.reason if c else None) for p, c in rows] == [
        ("gprop_open", None),
        ("gprop_closed", "not worth it"),
    ]


def test_list_for_routine_defaults_to_open() -> None:
    proposals = _FakeReadGardenProposalRepo(by_routine={"nightly": [_proposal("gprop_1"), _proposal("gprop_2")]})
    closures = _FakeGardenProposalClosureRepo(closed={"gprop_2": _closure("gprop_2")})
    reader = OpenGardenProposalReader(proposals=cast(Any, proposals), closures=cast(Any, closures))

    rows = reader.list_for_routine("nightly")

    assert [(p.proposal_id, c) for p, c in rows] == [("gprop_1", None)]
