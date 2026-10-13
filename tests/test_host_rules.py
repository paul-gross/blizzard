"""Host's lifecycle rules, pinned by value — no repository, no clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.hub.domain.runners.host import (
    HOST_VERBS,
    Host,
    HostHoldsRoutes,
    HostLifecycleFact,
    HostNotEnrolled,
    HostReport,
    HostRetired,
    HostState,
    HostTokenRevocation,
    HostTokenRotation,
    HostVerb,
    HostWorkspace,
    RunnerAlreadyHosted,
    RunnerNotUnderHost,
)
from blizzard.hub.domain.runners.registration import (
    STALE_AFTER,
    DeclaredSubscription,
    RunnerRegistration,
    RunnerRetired,
)
from blizzard.hub.domain.runners.route import Route

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_AT = datetime(2026, 2, 1, tzinfo=UTC)

_NEW = Host(host_id="hs_a", name="box", added_at=_T0, token_hash="hash")
_UNENROLLED = replace(_NEW, token_hash=None)
_RETIRED = replace(_UNENROLLED, retired=True, retired_at=_T0, retired_by="op")
_REPORT = HostReport(
    name="box",
    url="http://box",
    redirect_uris=("http://box/callback",),
    workspaces=(HostWorkspace("w1", 2),),
    subscriptions=(DeclaredSubscription("s", "S", "anthropic"),),
)
_REPORTED = replace(
    _NEW,
    registered_at=_T0,
    last_seen_at=_AT,
    url=_REPORT.url,
    redirect_uris=_REPORT.redirect_uris,
    workspaces=_REPORT.workspaces,
    subscriptions=_REPORT.subscriptions,
)
_NOW = _AT + timedelta(seconds=30)

_RUNNER = RunnerRegistration(runner_id="rn_a", name="a", added_at=_T0, hub_paused=False)
_HELD = Route(chunk_id="ch_1", runner_id="rn_a", workspace_id="w1", environment_ids=["e1"], created_at=_T0)


@pytest.mark.parametrize(
    ("host", "state"),
    [(_UNENROLLED, HostState.UNENROLLED), (_NEW, HostState.ENROLLED), (_RETIRED, HostState.RETIRED)],
)
def test_state_derives_from_retirement_first_then_the_token(host: Host, state: HostState) -> None:
    assert host.state() is state


def test_the_verb_table_gives_a_retired_host_only_retire_release_and_revoke_token() -> None:
    assert HOST_VERBS[HostState.RETIRED] == {HostVerb.RETIRE, HostVerb.RELEASE, HostVerb.REVOKE_TOKEN}


def test_every_live_state_admits_the_full_set_but_revoke_token_needs_a_token() -> None:
    every = set(HostVerb)
    assert HOST_VERBS[HostState.ENROLLED] == every
    assert HOST_VERBS[HostState.UNENROLLED] == every - {HostVerb.REVOKE_TOKEN}


def test_a_retired_host_refuses_contact_and_adding_a_runner_and_enrollment() -> None:
    with pytest.raises(HostRetired):
        _RETIRED.refuse_if_retired(action="report")
    with pytest.raises(HostRetired):
        _RETIRED.refuse_adding_runner()
    with pytest.raises(HostRetired):
        _RETIRED.enroll("h2", by="op", at=_AT)
    _NEW.refuse_if_retired(action="report")
    _NEW.refuse_adding_runner()


def test_enroll_rotates_and_revoke_needs_an_enrolled_token() -> None:
    assert _NEW.enroll("h2", by="op", at=_AT) == HostTokenRotation("hs_a", "h2", _AT, "op")
    assert _UNENROLLED.enroll("h2", by="op", at=_AT).token_hash == "h2"
    assert _NEW.revoke_token(by="op", at=_AT) == HostTokenRevocation("hs_a", _AT, "op")
    with pytest.raises(HostNotEnrolled):
        _UNENROLLED.revoke_token(by="op", at=_AT)
    with pytest.raises(HostNotEnrolled):
        _RETIRED.revoke_token(by="op", at=_AT)


def test_a_retired_host_with_a_token_an_enroll_left_behind_is_still_revocable() -> None:
    assert replace(_RETIRED, token_hash="late").revoke_token(by="op", at=_AT).host_id == "hs_a"


def test_adopt_admits_a_standalone_live_runner() -> None:
    _NEW.adopt(_RUNNER)


def test_adopt_refuses_a_hosted_runner_this_host_included() -> None:
    with pytest.raises(RunnerAlreadyHosted):
        _NEW.adopt(replace(_RUNNER, host_id="hs_other"))
    with pytest.raises(RunnerAlreadyHosted):
        _NEW.adopt(replace(_RUNNER, host_id="hs_a"))


def test_adopt_refuses_a_retired_runner_and_a_retired_host() -> None:
    with pytest.raises(RunnerRetired):
        _NEW.adopt(replace(_RUNNER, retired=True))
    with pytest.raises(HostRetired):
        _RETIRED.adopt(_RUNNER)


def test_release_admits_only_a_runner_under_this_host_even_from_a_retired_host() -> None:
    _NEW.release(replace(_RUNNER, host_id="hs_a"))
    _RETIRED.release(replace(_RUNNER, host_id="hs_a"))
    with pytest.raises(RunnerNotUnderHost):
        _NEW.release(_RUNNER)
    with pytest.raises(RunnerNotUnderHost):
        _NEW.release(replace(_RUNNER, host_id="hs_other"))


def test_the_first_report_is_a_change_and_an_identical_one_after_it_is_not() -> None:
    assert _NEW.report(_REPORT, other_live_names=(), now=_NOW).changed
    assert not _REPORTED.report(_REPORT, other_live_names=(), now=_NOW).changed


@pytest.mark.parametrize(
    "changed",
    [
        replace(_REPORT, url="http://elsewhere"),
        replace(_REPORT, redirect_uris=()),
        replace(_REPORT, workspaces=(HostWorkspace("w1", 3),)),
        replace(_REPORT, workspaces=()),
        replace(_REPORT, subscriptions=()),
        replace(_REPORT, name="renamed"),
    ],
)
def test_a_report_differing_in_a_held_field_is_a_change(changed: HostReport) -> None:
    assert _REPORTED.report(changed, other_live_names=(), now=_NOW).changed


def test_a_report_after_the_host_read_offline_is_a_change() -> None:
    assert _REPORTED.report(_REPORT, other_live_names=(), now=_AT + STALE_AFTER + timedelta(seconds=1)).changed


def test_a_blank_name_keeps_the_held_one_and_is_no_change() -> None:
    decision = _REPORTED.report(replace(_REPORT, name="  "), other_live_names=(), now=_NOW)
    assert decision.name is None and not decision.changed


def test_a_name_another_live_host_holds_is_not_recorded_and_is_no_change() -> None:
    decision = _REPORTED.report(replace(_REPORT, name="taken"), other_live_names={"taken"}, now=_NOW)
    assert decision.name is None and not decision.changed


def test_reporting_this_hosts_own_name_is_not_a_clash() -> None:
    assert _REPORTED.report(_REPORT, other_live_names={"box"}, now=_NOW).name == "box"


def test_a_retired_host_refuses_a_report() -> None:
    with pytest.raises(HostRetired):
        _RETIRED.report(_REPORT, other_live_names=(), now=_NOW)


def test_connection_is_never_connected_until_the_first_report_then_online_or_offline() -> None:
    assert _NEW.connection(now=_NOW) is RunnerConnection.NEVER_CONNECTED
    assert _REPORTED.connection(now=_NOW) is RunnerConnection.ONLINE
    assert _REPORTED.connection(now=_AT + STALE_AFTER + timedelta(seconds=1)) is RunnerConnection.OFFLINE


def test_a_first_retire_with_no_holdings_is_the_retire_fact() -> None:
    assert _NEW.retire({}, force=False, by="op", at=_AT) == HostLifecycleFact("hs_a", True, _AT, "op")
    assert _NEW.retire({"rn_a": []}, force=False, by="op", at=_AT) is not None


def test_a_first_retire_over_holdings_is_refused_naming_each_runners_chunks_unless_forced() -> None:
    holdings = {"rn_a": [_HELD], "rn_b": []}
    with pytest.raises(HostHoldsRoutes) as refused:
        _NEW.retire(holdings, force=False, by="op", at=_AT)
    assert refused.value.holdings == {"rn_a": [_HELD]}
    assert "ch_1" in str(refused.value) and "rn_a" in str(refused.value)
    assert _NEW.retire(holdings, force=True, by="op", at=_AT) is not None


def test_a_rerun_over_a_retired_host_writes_no_second_fact_and_needs_no_force() -> None:
    assert _RETIRED.retire({"rn_a": [_HELD]}, force=False, by="op", at=_AT) is None
