"""RunnerRegistration's lifecycle rules, pinned by value — no repository, no clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.hub.domain.runners.registration import (
    RUNNER_VERBS,
    STALE_AFTER,
    DeclaredSubscription,
    LifecycleFact,
    RunnerBinding,
    RunnerCapability,
    RunnerHoldsRoutes,
    RunnerLiveness,
    RunnerNotEnrolled,
    RunnerNotRetired,
    RunnerRegistration,
    RunnerReport,
    RunnerRetired,
    RunnerState,
    RunnerTokenRefused,
    RunnerVerb,
    ServedEntry,
    TokenRevocation,
    TokenRotation,
    UnregisteredRedirect,
    declared_name,
    refuse_runner_token,
)
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.domain.runners.version import VersionJudgement

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_AT = datetime(2026, 2, 1, tzinfo=UTC)

_UNENROLLED = RunnerRegistration(
    runner_id="runner-a",
    name="runner-a",
    added_at=_T0,
    workspace_id="ws-a",
    registered_at=_T0,
    last_seen_at=_T0,
    hub_paused=False,
    redirect_uris=("https://runner-a.example/callback",),
)
_ENROLLED = replace(_UNENROLLED, token_hash="hash-old")
_RETIRED = replace(_UNENROLLED, retired=True, retired_at=_T0, retired_by="op")
#: An enroll that raced the retire left a current token on a retired runner.
_RETIRED_WITH_TOKEN = replace(_RETIRED, token_hash="hash-old")
_NEVER_CONNECTED = RunnerRegistration(
    runner_id="runner-a", name="runner-a", added_at=_T0, hub_paused=False, token_hash="hash-old"
)

_HELD = Route(chunk_id="chk_1", runner_id="runner-a", workspace_id="ws-a", environment_ids=["e1"], created_at=_T0)


@pytest.mark.parametrize(
    ("registration", "state"),
    [(_UNENROLLED, RunnerState.UNENROLLED), (_ENROLLED, RunnerState.ENROLLED), (_RETIRED, RunnerState.RETIRED)],
)
def test_state_derives_from_retirement_then_the_token(registration: RunnerRegistration, state: RunnerState) -> None:
    assert registration.state() is state


def test_a_retired_runner_with_a_stale_token_hash_is_still_retired() -> None:
    assert replace(_RETIRED, token_hash="hash-old").state() is RunnerState.RETIRED


def test_the_verb_table_declares_every_state() -> None:
    assert set(RUNNER_VERBS) == set(RunnerState)
    assert RUNNER_VERBS[RunnerState.UNENROLLED] == {
        RunnerVerb.ENROLL,
        RunnerVerb.BRAKE,
        RunnerVerb.RETIRE,
        RunnerVerb.CONTACT,
    }
    assert RUNNER_VERBS[RunnerState.ENROLLED] == RUNNER_VERBS[RunnerState.UNENROLLED] | {RunnerVerb.REVOKE_TOKEN}
    assert RUNNER_VERBS[RunnerState.RETIRED] == {
        RunnerVerb.BRAKE,
        RunnerVerb.RETIRE,
        RunnerVerb.REINSTATE,
        RunnerVerb.REVOKE_TOKEN,
    }


def test_the_brake_is_legal_from_every_state() -> None:
    assert all(r.permits(RunnerVerb.BRAKE) for r in (_UNENROLLED, _ENROLLED, _RETIRED))


@pytest.mark.parametrize("registration", [_UNENROLLED, _ENROLLED])
def test_an_active_runners_contact_passes(registration: RunnerRegistration) -> None:
    registration.refuse_if_retired(action="heartbeat")


def test_a_retired_runners_contact_is_refused_naming_the_action() -> None:
    with pytest.raises(RunnerRetired) as refused:
        _RETIRED.refuse_if_retired(action="heartbeat")
    assert str(refused.value) == "runner runner-a is retired — heartbeat refused; `reinstate` it first"


# enroll


def test_enroll_mints_the_rotation_for_an_unenrolled_runner() -> None:
    assert _UNENROLLED.enroll("hash-new", by="operator", at=_AT) == TokenRotation(
        runner_id="runner-a", token_hash="hash-new", at=_AT, by="operator"
    )


def test_enroll_over_an_enrolled_runner_is_a_rotation() -> None:
    assert _ENROLLED.enroll("hash-new", by="operator", at=_AT).token_hash == "hash-new"


def test_enroll_refuses_a_retired_runner() -> None:
    with pytest.raises(RunnerRetired) as refused:
        _RETIRED.enroll("hash-new", by="operator", at=_AT)
    assert str(refused.value) == "runner runner-a is retired — enrollment refused; `reinstate` it first"


# revoke-token


def test_revoke_token_of_an_enrolled_runner_is_the_revocation() -> None:
    assert _ENROLLED.revoke_token(by="op", at=_AT) == TokenRevocation(runner_id="runner-a", at=_AT, by="op")


def test_revoke_token_of_a_retired_runner_still_holding_a_stale_hash_is_the_revocation() -> None:
    stale = replace(_RETIRED, token_hash="hash-old")
    assert stale.revoke_token(by="op", at=_AT) == TokenRevocation(runner_id="runner-a", at=_AT, by="op")


@pytest.mark.parametrize("registration", [_UNENROLLED, _RETIRED])
def test_revoke_token_refuses_a_runner_holding_no_token(registration: RunnerRegistration) -> None:
    with pytest.raises(RunnerNotEnrolled):
        registration.revoke_token(by="op", at=_AT)


# retire


@pytest.mark.parametrize("registration", [_UNENROLLED, _ENROLLED])
def test_a_first_retire_with_no_holdings_is_the_retire_fact(registration: RunnerRegistration) -> None:
    assert registration.retire((), force=False, by="op", at=_AT) == LifecycleFact(
        runner_id="runner-a", retired=True, at=_AT, by="op"
    )


def test_a_plain_first_retire_over_a_holding_is_refused_with_the_holdings() -> None:
    with pytest.raises(RunnerHoldsRoutes) as refused:
        _ENROLLED.retire([_HELD], force=False, by="op", at=_AT)

    assert refused.value.holdings == [_HELD]
    assert "chk_1 (environments: e1)" in str(refused.value)


def test_a_forced_first_retire_over_a_holding_is_the_retire_fact() -> None:
    fact = _ENROLLED.retire([_HELD], force=True, by="op", at=_AT)

    assert fact == LifecycleFact(runner_id="runner-a", retired=True, at=_AT, by="op")


@pytest.mark.parametrize("force", [False, True])
def test_a_rerun_over_a_retired_runner_writes_no_fact_even_while_holding(force: bool) -> None:
    assert _RETIRED.retire([_HELD], force=force, by="op", at=_AT) is None


# reinstate


def test_reinstate_of_a_retired_runner_is_the_reversal_fact() -> None:
    assert _RETIRED.reinstate(by="op", at=_AT) == LifecycleFact(runner_id="runner-a", retired=False, at=_AT, by="op")


@pytest.mark.parametrize("registration", [_UNENROLLED, _ENROLLED])
def test_reinstate_refuses_an_active_runner(registration: RunnerRegistration) -> None:
    with pytest.raises(RunnerNotRetired):
        registration.reinstate(by="op", at=_AT)


# federation


def test_federation_to_a_registered_redirect_passes() -> None:
    _ENROLLED.refuse_federation("https://runner-a.example/callback")


@pytest.mark.parametrize("registration", [_ENROLLED, _RETIRED])
def test_federation_to_an_unregistered_redirect_is_refused_before_retirement(
    registration: RunnerRegistration,
) -> None:
    with pytest.raises(UnregisteredRedirect):
        registration.refuse_federation("https://elsewhere.example/callback")


def test_federation_of_a_retired_runner_to_a_registered_redirect_is_refused_as_retired() -> None:
    with pytest.raises(RunnerRetired, match="federation refused"):
        _RETIRED.refuse_federation("https://runner-a.example/callback")


# a presented token


@pytest.mark.parametrize("current", [_ENROLLED, _NEVER_CONNECTED])
def test_a_current_token_of_a_runner_not_retired_names_that_runner(current: RunnerRegistration) -> None:
    assert refuse_runner_token(current, revoked_for=None) is current


@pytest.mark.parametrize(
    ("current", "revoked_for", "reason"),
    [
        (_RETIRED_WITH_TOKEN, None, RunnerTokenRefusalReason.RETIRED),
        (None, _RETIRED, RunnerTokenRefusalReason.RETIRED),
        (None, _UNENROLLED, RunnerTokenRefusalReason.REVOKED),
    ],
)
def test_a_refused_token_names_its_runner_and_reads_retired_before_revoked(
    current: RunnerRegistration | None, revoked_for: RunnerRegistration | None, reason: RunnerTokenRefusalReason
) -> None:
    with pytest.raises(RunnerTokenRefused) as refused:
        refuse_runner_token(current, revoked_for=revoked_for)
    assert (refused.value.reason, refused.value.runner_id) == (reason, "runner-a")


def test_a_token_neither_current_nor_revoked_is_unknown_and_names_no_runner() -> None:
    with pytest.raises(RunnerTokenRefused) as refused:
        refuse_runner_token(None, revoked_for=None)
    assert (refused.value.reason, refused.value.runner_id) == (RunnerTokenRefusalReason.UNKNOWN, None)


# names and connection


@pytest.mark.parametrize(
    ("declared", "recorded"),
    [("r-claude", "r-claude"), ("  r-claude ", "r-claude"), ("", None), ("  ", None), (None, None)],
)
def test_a_registration_records_its_stripped_name_and_keeps_the_held_one_for_a_blank(
    declared: str | None, recorded: str | None
) -> None:
    assert declared_name(declared) == recorded


def test_a_never_connected_runner_is_offline_even_with_a_last_seen_instant() -> None:
    liveness = RunnerLiveness.of(replace(_NEVER_CONNECTED, last_seen_at=_AT), now=_AT, threshold=STALE_AFTER)
    assert (liveness.online, liveness.connection()) == (False, RunnerConnection.NEVER_CONNECTED)


@pytest.mark.parametrize(("seen", "connection"), [(_AT, RunnerConnection.ONLINE), (_T0, RunnerConnection.OFFLINE)])
def test_a_registered_runner_is_online_or_offline_by_its_liveness(seen: datetime, connection: RunnerConnection) -> None:
    liveness = RunnerLiveness.of(replace(_ENROLLED, last_seen_at=seen), now=_AT, threshold=STALE_AFTER)
    assert liveness.connection() is connection


# registration changes

_JUDGED = VersionJudgement(reported_version="1.0.0", refusal=None, hub_version="1.0.0", floor="1.0.0", ceiling="1.0.0")
_SEEN = replace(
    _ENROLLED,
    last_seen_at=_AT,
    version_judged_at=_AT,
    reported_version="1.0.0",
    env_capacity=2,
    public_url="https://runner-a.example",
    gates=("review",),
    subscription="sub",
    served=(ServedEntry(entry="p", project_id="prj_1"),),
)
_SAME = RunnerReport(
    name=None,
    workspace_id="ws-a",
    env_capacity=2,
    public_url="https://runner-a.example",
    redirect_uris=("https://runner-a.example/callback",),
    capabilities=(),
    subscriptions=None,
    gates=("review",),
    bindings=None,
    subscription="sub",
)


def _changes(report: RunnerReport, *, held: RunnerRegistration = _SEEN, served=None, judgement=_JUDGED) -> bool:
    return held.registration_changes(report, served=served, judgement=judgement, now=_AT, threshold=STALE_AFTER)


def test_a_registration_repeating_what_is_held_changes_nothing() -> None:
    assert not _changes(_SAME)
    assert not _changes(_SAME, served=_SEEN.served)


@pytest.mark.parametrize(
    "report",
    [
        replace(_SAME, name="renamed"),
        replace(_SAME, workspace_id="ws-b"),
        replace(_SAME, env_capacity=3),
        replace(_SAME, public_url="https://elsewhere.example"),
        replace(_SAME, redirect_uris=()),
        replace(_SAME, capabilities=(RunnerCapability(harness_id="claude"),)),
        replace(_SAME, subscriptions=(DeclaredSubscription(slug="s", name="S", provider="p"),)),
        replace(_SAME, gates=()),
        replace(_SAME, bindings=(RunnerBinding(project="p", workspace_id="ws-a", agents=1),)),
        replace(_SAME, subscription=None),
        replace(_SAME, agents=2),
    ],
)
def test_a_registration_reporting_a_different_fact_changes_it(report: RunnerReport) -> None:
    assert _changes(report)


def test_a_blank_name_keeps_the_held_one_and_changes_nothing() -> None:
    assert not _changes(replace(_SAME, name=None))
    assert not _changes(replace(_SAME, name=_SEEN.name))


def test_a_differently_served_declaration_changes_it_and_none_keeps_the_held_one() -> None:
    assert _changes(_SAME, served=())
    assert not _changes(_SAME, served=None)


def test_a_new_version_judgement_changes_it() -> None:
    assert _changes(_SAME, judgement=replace(_JUDGED, reported_version="1.1.0"))
    assert _changes(_SAME, held=replace(_SEEN, version_judged_at=None))


def test_a_registration_from_a_stale_runner_changes_it() -> None:
    assert _changes(_SAME, held=replace(_SEEN, last_seen_at=_T0))
