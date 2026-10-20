from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.host_registration_state import HostRegistrationState
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.runner.hub.host_identity import HostIdentity, HostIdentityAlreadyRecorded, adopted
from blizzard.runner.hub.host_registration import HostRegistrationStatus
from blizzard.runner.hub.identity import IdentityVerdict, judged
from blizzard.runner.lifecycle.identity_check import (
    ForeignStore,
    ForeignStoreKind,
    TickScope,
    foreign_store,
    tick_scope,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 1, 12, tzinfo=UTC)
_RESET = "set BZ_HUB_TOKEN_R1"


def _verdict(state: IdentityState, *, reason: str | None = None) -> IdentityVerdict:
    return IdentityVerdict(
        state=state,
        store_runner_id="rn_1",
        token_runner_id="rn_2",
        checked_at=_AT,
        reset_command=_RESET,
        reason=reason,
    )


def test_a_token_naming_the_configured_runner_is_matched_and_any_other_mismatched() -> None:
    assert judged("rn_1", "rn_1") is IdentityState.MATCHED
    assert judged("rn_1", "rn_2") is IdentityState.MISMATCHED


@pytest.mark.parametrize(
    ("state", "refusal", "remedy"),
    [
        (IdentityState.MISMATCHED, "token belongs to runner rn_2, not the configured rn_1", _RESET),
        (IdentityState.REFUSED, "why", _RESET),
        (IdentityState.NOT_HELD, "why", None),
        (IdentityState.TENANT_DIFFERS, "why", None),
        (IdentityState.VERSION_REFUSED, "why", None),
        (IdentityState.MATCHED, None, None),
        (IdentityState.UNCHECKED, None, None),
    ],
)
def test_a_verdict_says_why_it_refuses_and_names_the_reset_only_for_the_token(
    state: IdentityState, refusal: str | None, remedy: str | None
) -> None:
    verdict = _verdict(state, reason="why")
    assert (verdict.refusal(), verdict.remedy()) == (refusal, remedy)


def test_a_refusing_verdict_with_no_reason_falls_back_to_its_state() -> None:
    assert _verdict(IdentityState.NOT_HELD).refusal() == "not_held"


def test_a_first_answer_is_adopted_and_a_matching_one_keeps_the_held_identity() -> None:
    first = adopted(None, "hs_1", "tn_1", _AT)
    assert first == HostIdentity("hs_1", "tn_1", _AT)
    assert adopted(first, "hs_1", "tn_1", datetime(2027, 1, 1, tzinfo=UTC)) is first


@pytest.mark.parametrize(("host_id", "tenant_id"), [("hs_2", "tn_1"), ("hs_1", "tn_2")])
def test_an_answer_naming_another_host_or_tenant_is_refused_naming_both(host_id: str, tenant_id: str) -> None:
    held = HostIdentity("hs_1", "tn_1", _AT)
    with pytest.raises(HostIdentityAlreadyRecorded) as refused:
        adopted(held, host_id, tenant_id, _AT)
    assert refused.value.refusal() == (
        f"this host is recorded as hs_1 in tn_1, but its hub answers as {host_id} in {tenant_id}"
    )


@pytest.mark.parametrize("state", list(HostRegistrationState))
def test_only_a_registered_host_is_answered_as_itself_and_drains(state: HostRegistrationState) -> None:
    status = HostRegistrationStatus(state=state)
    assert status.answered_as_this_host() is (state is HostRegistrationState.REGISTERED)
    assert status.drains() is status.answered_as_this_host()


def test_a_store_naming_only_our_runners_or_none_is_not_foreign() -> None:
    assert foreign_store((), {"rn_1"}) is None
    assert foreign_store(("rn_1", "rn_2"), {"rn_1", "rn_2"}) is None


def test_a_store_naming_ours_beside_a_foreign_runner_is_mixed_and_one_naming_only_foreign_runners_alien() -> None:
    assert foreign_store(("rn_1", "rn_x"), {"rn_1"}) == ForeignStore(
        kind=ForeignStoreKind.MIXED, named=("rn_1", "rn_x"), foreign=("rn_x",)
    )
    assert foreign_store(("rn_x", "rn_y"), {"rn_1"}) == ForeignStore(
        kind=ForeignStoreKind.ALIEN, named=("rn_x", "rn_y"), foreign=("rn_x", "rn_y")
    )


@pytest.mark.parametrize(
    ("state", "registration_checks", "scope"),
    [
        (IdentityState.MATCHED, False, TickScope.RUN_ALL),
        (IdentityState.UNCHECKED, True, TickScope.RUN_ALL),
        (IdentityState.MISMATCHED, False, TickScope.SKIP),
        (IdentityState.REFUSED, False, TickScope.SKIP),
        (IdentityState.NOT_HELD, False, TickScope.SKIP),
        (IdentityState.REFUSED, True, TickScope.REGISTRATION_ONLY),
        (IdentityState.VERSION_REFUSED, False, TickScope.REGISTRATION_ONLY),
    ],
)
def test_a_tick_runs_every_step_unless_refusing_and_the_registration_alone_when_it_can_lift_the_refusal(
    state: IdentityState, registration_checks: bool, scope: TickScope
) -> None:
    assert tick_scope(_verdict(state), registration_checks=registration_checks) is scope
