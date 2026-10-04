"""Garden-proposal rules pinned by value (unit tier): the verb-by-state table, the
authoring refusals and their order, the closure factories, and the delivery-closure
decision — each over loaded objects and a passed instant, with no repository or clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.garden_proposals import (
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
    GardenProposalOrigin,
)
from blizzard.hub.domain.chunk.model import HubWorkItem, WorkItemAuthor, WorkRef
from blizzard.hub.domain.garden.findings.model import Finding
from blizzard.hub.domain.garden.proposals.closure import (
    GardenProposalBodyWithoutMint,
    GardenProposalClosure,
    GardenProposalPassReasonRequired,
    MintingAccept,
    _compose_minted_body,
    accept_reason,
)
from blizzard.hub.domain.garden.proposals.model import (
    GARDEN_PROPOSAL_TRANSITIONS,
    DuplicateProposalFindingError,
    GardenProposal,
    GardenProposalAlreadyClosed,
    GardenProposalBlankFieldError,
    GardenProposalEdit,
    GardenProposalEmptyEditError,
    GardenProposalFindingAlreadyLinkedError,
    GardenProposalFindingExitedError,
    GardenProposalFindingNotLinkedError,
    GardenProposalNoFindingsError,
    GardenProposalState,
    GardenProposalVerb,
    RoutineProposalState,
    citable_finding_ids,
    garden_proposal_state,
    pair_with_closures,
    require_text,
)
from blizzard.hub.domain.garden.proposals.resolution import DeliveryClosure, delivery_closure, ordered_findings
from blizzard.hub.domain.kernel.unset import UNSET

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, tzinfo=UTC)


def _proposal(*, findings: list[str] | None = None) -> GardenProposal:
    return GardenProposal(
        proposal_id="gprop_1",
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        created_at=_T0,
        findings=["fin_1"] if findings is None else findings,
    )


def _finding(finding_id: str, *, state: str = "live") -> Finding:
    return Finding(
        finding_id=finding_id,
        routine_name="nightly",
        scope_slug="blizzard",
        class_="c",
        locus="l",
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


def _closure(
    kind: GardenProposalClosureKind = GardenProposalClosureKind.PASSED,
    outcome: GardenProposalItemOutcome | None = None,
) -> GardenProposalClosure:
    minted = outcome is GardenProposalItemOutcome.MINTED
    return GardenProposalClosure(
        proposal_id="gprop_1",
        closure=kind,
        reason="r",
        closed_by="u0",
        closed_at=_T0,
        item_outcome=outcome,
        source="hub" if minted else None,
        ref="7" if minted else None,
    )


_PASSED = _closure()
_MINTED = _closure(GardenProposalClosureKind.ACCEPTED, GardenProposalItemOutcome.MINTED)
_DECLINED = _closure(GardenProposalClosureKind.ACCEPTED, GardenProposalItemOutcome.DECLINED)
_CLOSED = [_PASSED, _MINTED, _DECLINED]


# --- The verb-by-state table --------------------------------------------------


def test_open_allows_every_editing_and_closing_verb_and_not_delivery() -> None:
    assert GARDEN_PROPOSAL_TRANSITIONS[GardenProposalState.OPEN] == {
        GardenProposalVerb.EDIT,
        GardenProposalVerb.ATTACH,
        GardenProposalVerb.DETACH,
        GardenProposalVerb.PASS,
        GardenProposalVerb.ACCEPT,
    }


def test_every_closed_state_is_terminal_and_only_a_minting_accept_takes_delivery() -> None:
    assert GARDEN_PROPOSAL_TRANSITIONS[GardenProposalState.PASSED] == frozenset()
    assert GARDEN_PROPOSAL_TRANSITIONS[GardenProposalState.ACCEPTED_DECLINED] == frozenset()
    assert GARDEN_PROPOSAL_TRANSITIONS[GardenProposalState.ACCEPTED_MINTED] == {GardenProposalVerb.DELIVER}


def test_the_table_names_every_state() -> None:
    assert set(GARDEN_PROPOSAL_TRANSITIONS) == set(GardenProposalState)


@pytest.mark.parametrize(
    ("closure", "state"),
    [
        (None, GardenProposalState.OPEN),
        (_PASSED, GardenProposalState.PASSED),
        (_MINTED, GardenProposalState.ACCEPTED_MINTED),
        (_DECLINED, GardenProposalState.ACCEPTED_DECLINED),
    ],
)
def test_state_derives_from_the_closure_row(closure: GardenProposalClosure | None, state: GardenProposalState) -> None:
    if closure is None:
        assert garden_proposal_state(None, None) is state
    else:
        assert closure.state is state


def test_an_accepted_closure_with_no_item_outcome_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="no item_outcome"):
        garden_proposal_state(GardenProposalClosureKind.ACCEPTED, None)


def test_only_a_minting_accept_mints_delivery() -> None:
    assert [c.mints_delivery for c in _CLOSED] == [False, True, False]


@pytest.mark.parametrize("closure", _CLOSED)
@pytest.mark.parametrize(
    "verb",
    [
        GardenProposalVerb.EDIT,
        GardenProposalVerb.ATTACH,
        GardenProposalVerb.DETACH,
        GardenProposalVerb.PASS,
        GardenProposalVerb.ACCEPT,
    ],
)
def test_every_verb_on_a_closed_proposal_is_refused_naming_the_closure(
    closure: GardenProposalClosure, verb: GardenProposalVerb
) -> None:
    with pytest.raises(GardenProposalAlreadyClosed) as excinfo:
        _proposal().require_legal(verb, closure)

    assert excinfo.value.closure == closure


# --- Text and citation rules --------------------------------------------------


def test_require_text_strips_and_refuses_blank() -> None:
    assert require_text("  a title ", "title") == "a title"
    with pytest.raises(GardenProposalBlankFieldError) as excinfo:
        require_text(" \n ", "class")
    assert excinfo.value.field_name == "class"


def test_citable_ids_refuse_a_repeat_before_an_exit() -> None:
    exited = _finding("fin_2", state="wont-fix")

    with pytest.raises(DuplicateProposalFindingError):
        citable_finding_ids([_finding("fin_1"), _finding("fin_1"), exited], require_unexited=True)
    with pytest.raises(GardenProposalFindingExitedError):
        citable_finding_ids([_finding("fin_1"), exited], require_unexited=True)


def test_citable_ids_admit_gone_delivered_and_when_asked_exited_findings() -> None:
    findings = [_finding("fin_1", state="gone"), _finding("fin_2", state="delivered")]

    assert citable_finding_ids(findings, require_unexited=True) == ["fin_1", "fin_2"]
    assert citable_finding_ids([_finding("fin_3", state="resolved")], require_unexited=False) == ["fin_3"]


# --- Operator create ----------------------------------------------------------


def test_operator_proposal_carries_its_stripped_fields_and_cited_ids() -> None:
    proposal = GardenProposal.operator(
        "gprop_9",
        created_by="u1",
        routine_name="nightly",
        class_=" c ",
        title=" t ",
        body=" b ",
        findings=[_finding("fin_1")],
        at=_T1,
    )

    assert proposal == GardenProposal(
        proposal_id="gprop_9",
        origin=GardenProposalOrigin.OPERATOR,
        routine_name="nightly",
        class_="c",
        title="t",
        body="b",
        created_at=_T1,
        created_by="u1",
        findings=["fin_1"],
    )


@pytest.mark.parametrize(
    ("title", "class_", "body", "field_name"),
    [(" ", " ", " ", "title"), ("t", " ", " ", "class"), ("t", "c", " ", "body")],
)
def test_operator_refuses_blank_title_then_class_then_body(title: str, class_: str, body: str, field_name: str) -> None:
    with pytest.raises(GardenProposalBlankFieldError) as excinfo:
        GardenProposal.operator(
            "gprop_9",
            created_by="u1",
            routine_name=None,
            class_=class_,
            title=title,
            body=body,
            findings=[_finding("fin_1", state="superseded")],
            at=_T1,
        )
    assert excinfo.value.field_name == field_name


def test_operator_refuses_an_exited_finding_once_the_text_is_valid() -> None:
    with pytest.raises(GardenProposalFindingExitedError):
        GardenProposal.operator(
            "gprop_9",
            created_by="u1",
            routine_name=None,
            class_="c",
            title="t",
            body="b",
            findings=[_finding("fin_1", state="superseded")],
            at=_T1,
        )


# --- Edit ---------------------------------------------------------------------


def test_edit_strips_given_fields_and_leaves_the_rest_unset() -> None:
    fields = _proposal().edit_fields(GardenProposalEdit(title="  new  "), closure=None)

    assert fields == GardenProposalEdit(title="new", class_=UNSET, body=UNSET)


def test_edit_refuses_closed_before_empty_and_empty_before_blank() -> None:
    with pytest.raises(GardenProposalAlreadyClosed):
        _proposal().edit_fields(GardenProposalEdit(), closure=_PASSED)
    with pytest.raises(GardenProposalEmptyEditError):
        _proposal().edit_fields(GardenProposalEdit(), closure=None)
    with pytest.raises(GardenProposalBlankFieldError) as excinfo:
        _proposal().edit_fields(GardenProposalEdit(class_=" ", body=" "), closure=None)
    assert excinfo.value.field_name == "class"


# --- Attach and detach --------------------------------------------------------


def test_attach_returns_the_ids_to_link() -> None:
    assert _proposal().attachable([_finding("fin_2"), _finding("fin_3", state="gone")], closure=None) == [
        "fin_2",
        "fin_3",
    ]


def test_attach_refuses_closed_then_empty_then_repeat_then_exited_then_already_linked() -> None:
    proposal = _proposal(findings=["fin_1"])

    with pytest.raises(GardenProposalAlreadyClosed):
        proposal.attachable([], closure=_DECLINED)
    with pytest.raises(GardenProposalNoFindingsError):
        proposal.attachable([], closure=None)
    with pytest.raises(DuplicateProposalFindingError):
        proposal.attachable([_finding("fin_1"), _finding("fin_1")], closure=None)
    with pytest.raises(GardenProposalFindingExitedError):
        proposal.attachable([_finding("fin_1", state="resolved")], closure=None)
    with pytest.raises(GardenProposalFindingAlreadyLinkedError):
        proposal.attachable([_finding("fin_1")], closure=None)


def test_detach_returns_the_ids_to_unlink_even_when_exited() -> None:
    proposal = _proposal(findings=["fin_1", "fin_2"])

    assert proposal.detachable([_finding("fin_2", state="wont-fix")], closure=None) == ["fin_2"]


def test_detach_refuses_closed_then_empty_then_repeat_then_not_linked() -> None:
    proposal = _proposal(findings=["fin_1"])

    with pytest.raises(GardenProposalAlreadyClosed):
        proposal.detachable([], closure=_MINTED)
    with pytest.raises(GardenProposalNoFindingsError):
        proposal.detachable([], closure=None)
    with pytest.raises(DuplicateProposalFindingError):
        proposal.detachable([_finding("fin_9"), _finding("fin_9")], closure=None)
    with pytest.raises(GardenProposalFindingNotLinkedError):
        proposal.detachable([_finding("fin_9")], closure=None)


# --- Routine read pairing -----------------------------------------------------


def test_pairing_by_state() -> None:
    a = _proposal()
    b = GardenProposal(**{**a.__dict__, "proposal_id": "gprop_2"})
    closures = {"gprop_2": _PASSED}

    assert pair_with_closures([a, b], closures, RoutineProposalState.OPEN) == [(a, None), (b, None)]
    assert pair_with_closures([a, b], closures, RoutineProposalState.CLOSED) == [(b, _PASSED)]
    assert pair_with_closures([a, b], closures, RoutineProposalState.ALL) == [(a, None), (b, _PASSED)]


# --- Pass ---------------------------------------------------------------------


def test_passing_stores_the_reason_stripped() -> None:
    closure = GardenProposalClosure.passing(_proposal(), None, reason="  not worth it ", by="u1", at=_T1)

    assert closure == GardenProposalClosure(
        proposal_id="gprop_1",
        closure=GardenProposalClosureKind.PASSED,
        reason="not worth it",
        closed_by="u1",
        closed_at=_T1,
        item_outcome=None,
        source=None,
        ref=None,
    )


def test_passing_a_closed_proposal_is_refused_before_a_blank_reason() -> None:
    with pytest.raises(GardenProposalAlreadyClosed):
        GardenProposalClosure.passing(_proposal(), _PASSED, reason="  ", by="u1", at=_T1)
    with pytest.raises(GardenProposalPassReasonRequired):
        GardenProposalClosure.passing(_proposal(), None, reason="  ", by="u1", at=_T1)


# --- Accept -------------------------------------------------------------------


@pytest.mark.parametrize(("given", "stored"), [(None, None), ("   ", None), ("  ok ", "ok")])
def test_an_accept_reason_is_stripped_and_a_blank_one_is_none(given: str | None, stored: str | None) -> None:
    assert accept_reason(given) == stored


def test_declining_accept_records_declined_with_its_normalized_reason() -> None:
    closure = GardenProposalClosure.accepted_declining(_proposal(), None, reason="  ", body=None, by="u1", at=_T1)

    assert closure == GardenProposalClosure(
        proposal_id="gprop_1",
        closure=GardenProposalClosureKind.ACCEPTED,
        reason=None,
        closed_by="u1",
        closed_at=_T1,
        item_outcome=GardenProposalItemOutcome.DECLINED,
        source=None,
        ref=None,
    )


def test_declining_accept_refuses_closed_before_a_body_override() -> None:
    with pytest.raises(GardenProposalAlreadyClosed):
        GardenProposalClosure.accepted_declining(_proposal(), _PASSED, reason=None, body="b", by="u1", at=_T1)
    with pytest.raises(GardenProposalBodyWithoutMint):
        GardenProposalClosure.accepted_declining(_proposal(), None, reason=None, body="b", by="u1", at=_T1)


def test_minting_accept_wraps_the_override_or_the_proposals_own_body() -> None:
    findings = [_finding("fin_1")]

    own = MintingAccept.of(_proposal(), None, reason=" why ", body=None, findings=findings)
    override = MintingAccept.of(_proposal(), None, reason=None, body="hand-drafted", findings=findings)

    assert own == MintingAccept(
        proposal_id="gprop_1",
        title="Author a docstring standard",
        body=_compose_minted_body("the case", findings),
        reason="why",
    )
    assert override.body == _compose_minted_body("hand-drafted", findings)


def test_minting_accept_refuses_a_closed_proposal() -> None:
    with pytest.raises(GardenProposalAlreadyClosed):
        MintingAccept.of(_proposal(), _MINTED, reason=None, body=None, findings=[])


def test_minted_closure_points_at_the_item_and_takes_its_instant() -> None:
    accept = MintingAccept(proposal_id="gprop_1", title="t", body="b", reason="why")
    item = HubWorkItem(
        work_item_id="wi_1",
        source="hub",
        ref="7",
        title="t",
        body="b",
        author=WorkItemAuthor.user("u1"),
        stated_priority=None,
        created_at=_T1,
        edited_at=_T1,
    )

    assert GardenProposalClosure.accepted_minting(accept, item, by="u1") == GardenProposalClosure(
        proposal_id="gprop_1",
        closure=GardenProposalClosureKind.ACCEPTED,
        reason="why",
        closed_by="u1",
        closed_at=_T1,
        item_outcome=GardenProposalItemOutcome.MINTED,
        source="hub",
        ref="7",
    )


# --- Delivery closure ---------------------------------------------------------


def test_ordered_findings_keep_the_proposals_order_and_drop_unresolved_ids() -> None:
    by_id = {f.finding_id: f for f in [_finding("fin_2"), _finding("fin_1", state="gone")]}

    assert [f.finding_id for f in ordered_findings(["fin_1", "fin_x", "fin_2"], by_id)] == ["fin_1", "fin_2"]
    assert [f.finding_id for f in ordered_findings(["fin_1", "fin_2"], by_id, live_only=True)] == ["fin_2"]


def test_delivery_closes_only_the_still_live_findings_on_the_accepters_behalf() -> None:
    proposal = _proposal(findings=["fin_1", "fin_2", "fin_3", "fin_4"])
    live_1, live_3 = _finding("fin_1"), _finding("fin_3")
    by_id = {
        "fin_1": live_1,
        "fin_2": _finding("fin_2", state="delivered"),
        "fin_3": live_3,
        "fin_4": _finding("fin_4", state="wont-fix"),
    }

    decided = delivery_closure(_MINTED, proposal, by_id, WorkRef(source="hub", ref="7"))

    assert decided == DeliveryClosure(
        findings=[live_1, live_3], note="delivered by hub:7", actor="u0", proposal_id="gprop_1"
    )


@pytest.mark.parametrize("closure", [_PASSED, _DECLINED])
def test_delivery_reaches_nothing_through_a_closure_that_minted_no_item(closure: GardenProposalClosure) -> None:
    by_id = {"fin_1": _finding("fin_1")}

    assert delivery_closure(closure, _proposal(), by_id, WorkRef(source="hub", ref="7")) is None


def test_delivery_with_no_live_finding_closes_nothing() -> None:
    by_id = {"fin_1": _finding("fin_1", state="gone")}

    assert delivery_closure(_MINTED, _proposal(), by_id, WorkRef(source="hub", ref="7")) is None
