"""Permit's lifecycle rules, pinned by value — no repository, no clock."""

# exemplar: bzh:domain-orchestration-split — the model's rules proved without a seam.

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.projects.model import ProjectId
from exemplar.foundation.permits import PermitState, PermitVerb
from exemplar.hub.domain.permits.model import (
    PERMIT_VERB_LEGALITY,
    Permit,
    PermitFact,
    PermitFactKind,
    PermitVerbRefused,
    RevocationNoteRequired,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_AT = datetime(2026, 2, 1, tzinfo=UTC)

_REQUESTED = Permit.request("pm_a", project_id=ProjectId("proj_a"), holder="ada", capability="deploy", by="op", at=_T0)
_GRANTED = replace(_REQUESTED, latest=PermitFact("pm_a", PermitFactKind.GRANTED, at=_T0, by="op"))
_SUSPENDED = replace(_REQUESTED, latest=PermitFact("pm_a", PermitFactKind.SUSPENDED, at=_T0, by="op"))
_RESUMED = replace(_REQUESTED, latest=PermitFact("pm_a", PermitFactKind.RESUMED, at=_T0, by="op"))
_REVOKED = replace(_REQUESTED, latest=PermitFact("pm_a", PermitFactKind.REVOKED, at=_T0, by="op", note="n"))

_IN_STATE = {
    PermitState.REQUESTED: _REQUESTED,
    PermitState.GRANTED: _GRANTED,
    PermitState.SUSPENDED: _SUSPENDED,
    PermitState.REVOKED: _REVOKED,
}


def _act(permit: Permit, verb: PermitVerb) -> PermitFact:
    match verb:
        case PermitVerb.GRANT:
            return permit.grant(by="op", at=_AT)
        case PermitVerb.SUSPEND:
            return permit.suspend(by="op", at=_AT)
        case PermitVerb.RESUME:
            return permit.resume(by="op", at=_AT)
        case PermitVerb.REVOKE:
            return permit.revoke(note="done", by="op", at=_AT)


@pytest.mark.parametrize(
    ("permit", "state"),
    [
        (_REQUESTED, PermitState.REQUESTED),
        (_GRANTED, PermitState.GRANTED),
        (_SUSPENDED, PermitState.SUSPENDED),
        (_RESUMED, PermitState.GRANTED),
        (_REVOKED, PermitState.REVOKED),
    ],
)
def test_state_derives_from_the_newest_fact(permit: Permit, state: PermitState) -> None:
    assert permit.state() is state


@pytest.mark.parametrize(
    ("verb", "legal"),
    [
        (PermitVerb.GRANT, frozenset({PermitState.REQUESTED})),
        (PermitVerb.SUSPEND, frozenset({PermitState.GRANTED})),
        (PermitVerb.RESUME, frozenset({PermitState.SUSPENDED})),
        (PermitVerb.REVOKE, frozenset({PermitState.REQUESTED, PermitState.GRANTED, PermitState.SUSPENDED})),
    ],
)
def test_each_verb_is_legal_from_exactly_its_states(verb: PermitVerb, legal: frozenset[PermitState]) -> None:
    assert PERMIT_VERB_LEGALITY[verb] == legal
    for state, permit in _IN_STATE.items():
        assert permit.permits(verb) is (state in legal)
        if state in legal:
            assert _act(permit, verb).at == _AT
        else:
            with pytest.raises(PermitVerbRefused):
                _act(permit, verb)


def test_legal_verbs_lists_what_permits_allows_in_declaration_order() -> None:
    assert _REQUESTED.legal_verbs() == (PermitVerb.GRANT, PermitVerb.REVOKE)
    assert _GRANTED.legal_verbs() == (PermitVerb.SUSPEND, PermitVerb.REVOKE)
    assert _SUSPENDED.legal_verbs() == (PermitVerb.RESUME, PermitVerb.REVOKE)
    assert _REVOKED.legal_verbs() == ()


@pytest.mark.parametrize("verb", list(PermitVerb))
def test_a_revoked_permit_refuses_every_verb(verb: PermitVerb) -> None:
    with pytest.raises(PermitVerbRefused) as refused:
        _act(_REVOKED, verb)

    assert (refused.value.permit_id, refused.value.verb, refused.value.state) == ("pm_a", verb, PermitState.REVOKED)


@pytest.mark.parametrize(
    ("permit", "verb", "kind"),
    [
        (_REQUESTED, PermitVerb.GRANT, PermitFactKind.GRANTED),
        (_GRANTED, PermitVerb.SUSPEND, PermitFactKind.SUSPENDED),
        (_SUSPENDED, PermitVerb.RESUME, PermitFactKind.RESUMED),
        (_GRANTED, PermitVerb.REVOKE, PermitFactKind.REVOKED),
    ],
)
def test_each_verb_decides_its_own_fact_kind(permit: Permit, verb: PermitVerb, kind: PermitFactKind) -> None:
    assert _act(permit, verb).kind is kind


def test_a_revocation_carries_its_note_stripped() -> None:
    assert _GRANTED.revoke(note="  leaked  ", by="op", at=_AT).note == "leaked"


@pytest.mark.parametrize("note", ["", "   "])
def test_a_blank_revocation_note_is_refused(note: str) -> None:
    with pytest.raises(RevocationNoteRequired) as refused:
        _GRANTED.revoke(note=note, by="op", at=_AT)

    assert refused.value.permit_id == "pm_a"
