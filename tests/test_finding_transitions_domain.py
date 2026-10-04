"""A finding's verb rules on the model (unit tier, by value — no repository, no clock):
the declared verb-by-state table, each person's verb returning its fact or refusing, the
bulk verbs' batch order, and a run's `gone` settling a delivered finding."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.garden.findings.bucket import FindingBucket
from blizzard.hub.domain.garden.findings.model import (
    EXIT_KINDS,
    FINDING_STATES,
    FINDING_TRANSITIONS,
    AbsorberNotLive,
    DuplicateFindingError,
    FactEntry,
    Finding,
    FindingAlreadyExited,
    FindingNoteRequiredError,
    FindingNotReopenable,
    FindingSupersedesItself,
    UnknownFactKindError,
    deliver_facts,
    exit_facts,
    require_note,
    supersede_facts,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_PERSON_EXITS = sorted(EXIT_KINDS - {"superseded"})


def _finding(finding_id: str = "fin_1", *, state: str = "live", actor: str | None = None) -> Finding:
    return Finding(
        finding_id=finding_id,
        routine_name="nightly",
        scope_slug="runner",
        class_="c",
        locus="a.py:1",
        summary="s",
        introduced=None,
        introduced_at=None,
        first_observed_at=_AT,
        live=state == "live",
        state=state,
        note=None,
        last_seen_at=_AT,
        observed_count=0,
        actor=actor,
    )


# --- the declared table -------------------------------------------------------------


def test_the_table_names_only_known_states() -> None:
    for states in FINDING_TRANSITIONS.values():
        assert states <= FINDING_STATES


@pytest.mark.parametrize(
    ("kind", "legal"),
    [
        ("observed", {"live", "gone", "delivered"}),
        ("gone", {"live", "gone", "delivered"}),
        ("delivered", {"live"}),
        ("resolved", {"live", "gone", "delivered"}),
        ("gone-confirmed", {"live", "gone", "delivered"}),
        ("wont-fix", {"live", "gone", "delivered"}),
        ("not-a-finding", {"live", "gone", "delivered"}),
        ("superseded", {"live", "gone", "delivered"}),
        ("reopened", {"gone", "delivered", *EXIT_KINDS}),
    ],
)
def test_each_verb_is_legal_from_exactly_its_declared_states(kind: str, legal: set[str]) -> None:
    assert {state for state in FINDING_STATES if _finding(state=state).allows(kind)} == legal


def test_add_has_no_prior_state() -> None:
    assert not any(_finding(state=state).allows("add") for state in FINDING_STATES)


def test_exited_and_delivered_read_the_state() -> None:
    assert _finding(state="resolved").exited
    assert not _finding(state="gone").exited
    assert _finding(state="delivered").delivered
    assert not _finding(state="live").delivered


# --- a person's exit and reopen -----------------------------------------------------


@pytest.mark.parametrize("state", ["live", "gone", "delivered"])
@pytest.mark.parametrize("kind", _PERSON_EXITS)
def test_an_exit_from_an_unexited_state_returns_its_fact(kind: str, state: str) -> None:
    fact = _finding(state=state).exit_fact(kind, note="  why  ", actor="u1", at=_AT)

    assert fact == FactEntry(finding_id="fin_1", kind=kind, at=_AT, note="why", actor="u1")


def test_a_resolve_carries_its_proposal_id() -> None:
    fact = _finding().exit_fact("resolved", note="n", actor="u1", at=_AT, proposal_id="gp_1")

    assert fact.proposal_id == "gp_1"


@pytest.mark.parametrize("exited", sorted(EXIT_KINDS))
@pytest.mark.parametrize("kind", _PERSON_EXITS)
def test_an_exit_on_an_exited_finding_is_refused(kind: str, exited: str) -> None:
    with pytest.raises(FindingAlreadyExited) as info:
        _finding(state=exited).exit_fact(kind, note="n", actor="u1", at=_AT)

    assert (info.value.finding_id, info.value.kind, info.value.state) == ("fin_1", kind, exited)


@pytest.mark.parametrize("state", ["gone", "delivered", *sorted(EXIT_KINDS)])
def test_reopen_from_any_non_live_state_returns_its_fact(state: str) -> None:
    fact = _finding(state=state).exit_fact("reopened", note="still there", actor="u1", at=_AT)

    assert fact == FactEntry(finding_id="fin_1", kind="reopened", at=_AT, note="still there", actor="u1")


def test_reopen_on_a_live_finding_is_refused() -> None:
    with pytest.raises(FindingNotReopenable):
        _finding(state="live").exit_fact("reopened", note="n", actor="u1", at=_AT)


@pytest.mark.parametrize("note", ["", "   "])
def test_a_blank_note_is_refused(note: str) -> None:
    with pytest.raises(FindingNoteRequiredError, match="'wont-fix'"):
        _finding().exit_fact("wont-fix", note=note, actor="u1", at=_AT)


def test_a_state_refusal_outranks_a_blank_note() -> None:
    with pytest.raises(FindingAlreadyExited):
        _finding(state="resolved").exit_fact("resolved", note="", actor="u1", at=_AT)


@pytest.mark.parametrize("kind", ["superseded", "delivered", "gone", "add"])
def test_exit_fact_writes_only_a_persons_exit_or_reopen(kind: str) -> None:
    with pytest.raises(UnknownFactKindError):
        _finding().exit_fact(kind, note="n", actor="u1", at=_AT)


def test_require_note_strips() -> None:
    assert require_note("resolved", "  ok ") == "ok"
    with pytest.raises(FindingNoteRequiredError):
        require_note("resolved", None)


# --- supersede ----------------------------------------------------------------------


def test_supersede_into_a_live_absorber_names_it() -> None:
    fact = _finding("fin_1").supersede_into(_finding("fin_2"), note="folded", actor="u1", at=_AT)

    assert fact == FactEntry(
        finding_id="fin_1", kind="superseded", at=_AT, note="folded", actor="u1", superseded_by="fin_2"
    )


def test_supersede_into_itself_is_refused() -> None:
    finding = _finding("fin_1")

    with pytest.raises(FindingSupersedesItself):
        finding.supersede_into(finding, note="n", actor="u1", at=_AT)


@pytest.mark.parametrize("state", ["gone", "delivered", *sorted(EXIT_KINDS)])
def test_supersede_into_a_non_live_absorber_is_refused(state: str) -> None:
    with pytest.raises(AbsorberNotLive) as info:
        _finding("fin_1").supersede_into(_finding("fin_2", state=state), note="n", actor="u1", at=_AT)

    assert info.value.finding_id == "fin_2"


def test_supersede_of_an_exited_finding_is_refused() -> None:
    with pytest.raises(FindingAlreadyExited):
        _finding("fin_1", state="wont-fix").supersede_into(_finding("fin_2"), note="n", actor="u1", at=_AT)


def test_supersede_into_an_absorber_in_another_scope_is_legal() -> None:
    absorber = Finding(**{**_finding("fin_2").__dict__, "scope_slug": "hub", "routine_name": "weekly"})

    fact = _finding("fin_1").supersede_into(absorber, note="n", actor="u1", at=_AT)

    assert fact.superseded_by == "fin_2"


# --- deliver ------------------------------------------------------------------------


def test_deliver_closes_a_live_finding() -> None:
    fact = _finding().deliver_fact(note=" landed ", actor="u1", at=_AT, proposal_id="gp_1")

    assert fact == FactEntry(
        finding_id="fin_1", kind="delivered", at=_AT, note="landed", actor="u1", proposal_id="gp_1"
    )


@pytest.mark.parametrize("state", ["gone", "delivered", *sorted(EXIT_KINDS)])
def test_deliver_leaves_a_non_live_finding_as_it_stands(state: str) -> None:
    assert _finding(state=state).deliver_fact(note="n", actor="u1", at=_AT) is None


def test_deliver_refuses_a_blank_note_even_when_it_would_skip() -> None:
    with pytest.raises(FindingNoteRequiredError):
        _finding(state="resolved").deliver_fact(note=" ", actor="u1", at=_AT)


# --- a run's gone -------------------------------------------------------------------


def test_a_runs_gone_settles_a_delivered_finding_to_resolved_carrying_its_closer() -> None:
    assert _finding(state="delivered", actor="closer").run_gone() == ("resolved", "closer")


@pytest.mark.parametrize("state", ["live", "gone"])
def test_a_runs_gone_flags_any_other_finding_gone(state: str) -> None:
    assert _finding(state=state).run_gone() == ("gone", None)


# --- the bulk verbs -----------------------------------------------------------------


def test_exit_facts_returns_one_fact_per_finding_in_order() -> None:
    facts = exit_facts([_finding("fin_1"), _finding("fin_2", state="gone")], "resolved", note="n", actor="u1", at=_AT)

    assert [(f.finding_id, f.kind) for f in facts] == [("fin_1", "resolved"), ("fin_2", "resolved")]


def test_exit_facts_refuses_a_duplicate_id() -> None:
    with pytest.raises(DuplicateFindingError) as info:
        exit_facts([_finding("fin_1"), _finding("fin_1")], "wont-fix", note="n", actor="u1", at=_AT)

    assert info.value.finding_id == "fin_1"


def test_exit_facts_refuses_the_whole_batch_for_one_exited_finding() -> None:
    with pytest.raises(FindingAlreadyExited):
        exit_facts([_finding("fin_1"), _finding("fin_2", state="resolved")], "resolved", note="n", actor="u1", at=_AT)


def test_exit_facts_checks_every_state_before_the_note() -> None:
    with pytest.raises(FindingAlreadyExited):
        exit_facts([_finding("fin_1"), _finding("fin_2", state="resolved")], "resolved", note="", actor="u1", at=_AT)


def test_exit_facts_refuses_a_blank_note_on_an_empty_batch() -> None:
    with pytest.raises(FindingNoteRequiredError):
        exit_facts([], "resolved", note=" ", actor="u1", at=_AT)


def test_exit_facts_on_an_empty_batch_is_empty() -> None:
    assert exit_facts([], "resolved", note="n", actor="u1", at=_AT) == []


def test_supersede_facts_refuses_a_self_naming_absorber_anywhere_in_the_batch() -> None:
    with pytest.raises(FindingSupersedesItself):
        supersede_facts([_finding("fin_1"), _finding("fin_2")], _finding("fin_2"), note="n", actor="u1", at=_AT)


def test_supersede_facts_refuses_a_dead_absorber_before_a_blank_note() -> None:
    with pytest.raises(AbsorberNotLive):
        supersede_facts([_finding("fin_1")], _finding("fin_2", state="gone"), note="", actor="u1", at=_AT)


def test_supersede_facts_refuses_a_duplicate_id() -> None:
    with pytest.raises(DuplicateFindingError):
        supersede_facts([_finding("fin_1"), _finding("fin_1")], _finding("fin_2"), note="n", actor="u1", at=_AT)


def test_deliver_facts_writes_only_the_live_findings() -> None:
    facts = deliver_facts(
        [_finding("fin_1"), _finding("fin_2", state="gone"), _finding("fin_3", state="wont-fix")],
        note="n",
        actor="u1",
        at=_AT,
        proposal_id="gp_1",
    )

    assert [f.finding_id for f in facts] == ["fin_1"]


@pytest.mark.parametrize("state", sorted(FINDING_STATES))
def test_a_run_may_cite_exactly_the_findings_the_table_lets_it_observe_or_flag_gone(state: str) -> None:
    finding = _finding(state=state)
    bucket = FindingBucket.of([finding], own_scope="runner")
    citable = finding in bucket.citable
    assert citable == finding.allows("observed") == finding.allows("gone")
    assert (finding.finding_id in bucket.exited_ids) is not citable
