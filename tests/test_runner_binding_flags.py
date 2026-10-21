"""The flags a host's report raises against a runner's bindings and subscription, and the projects the matched peek
may offer from them (unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.runner_version import VersionRefusalReason
from blizzard.hub.domain.runners.bindings import BindingFlags, claimable_projects, flag_bindings, offered_projects
from blizzard.hub.domain.runners.host import Host, HostWorkspace
from blizzard.hub.domain.runners.registration import (
    DeclaredSubscription,
    RunnerBinding,
    RunnerRegistration,
    ServedEntry,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 12, tzinfo=UTC)
_A = RunnerBinding("alpha", "ws-a", 1)
_X = RunnerBinding("beta", "ws-x", 1)


def _host(*workspaces: str, roster: tuple[str, ...] = ("max",), reported: bool = True) -> Host:
    return Host(
        host_id="hs_1",
        name="box",
        added_at=_AT,
        registered_at=_AT if reported else None,
        workspaces=tuple(HostWorkspace(w) for w in workspaces),
        subscriptions=tuple(DeclaredSubscription(slug, slug, "anthropic") for slug in roster),
    )


def _runner(
    bindings: tuple[RunnerBinding, ...] | None, subscription: str | None = None, served: tuple[ServedEntry, ...] = ()
) -> RunnerRegistration:
    return RunnerRegistration(
        runner_id="rn_1",
        name="r",
        added_at=_AT,
        registered_at=_AT,
        hub_paused=False,
        host_id="hs_1",
        bindings=bindings,
        subscription=subscription,
        served=served,
    )


def test_nothing_is_flagged_without_a_host() -> None:
    assert flag_bindings((_X,), "nope", None) == BindingFlags()


def test_nothing_is_flagged_while_the_host_has_never_reported() -> None:
    assert flag_bindings((_X,), "nope", _host(reported=False)) == BindingFlags()


def test_a_binding_is_flagged_when_its_workspace_is_not_among_the_reported() -> None:
    flags = flag_bindings((_A, _X), None, _host("ws-a"))

    assert not flags.workspace_unreported(_A)
    assert flags.workspace_unreported(_X)
    assert not flags.subscription_unrostered


def test_a_subscription_is_flagged_when_it_is_set_and_outside_the_roster() -> None:
    assert flag_bindings((), "other", _host("ws-a")).subscription_unrostered
    assert not flag_bindings((), "max", _host("ws-a")).subscription_unrostered
    assert not flag_bindings((), None, _host("ws-a", roster=())).subscription_unrostered


def test_a_flagged_subscription_withholds_every_binding() -> None:
    flags = flag_bindings((_A,), "other", _host("ws-a"))

    assert not flags.workspace_unreported(_A)
    assert flags.withholds(_A)


_SERVED = (ServedEntry("alpha", "prj_a"), ServedEntry("beta", "prj_b"), ServedEntry("old", None))


def test_a_runner_without_bindings_is_offered_every_resolved_project_it_serves() -> None:
    assert offered_projects(_runner(None, served=_SERVED), _host()) == {"prj_a", "prj_b"}


def test_a_project_reached_only_through_flagged_bindings_is_not_offered() -> None:
    runner = _runner((_A, _X), "max", _SERVED)

    assert offered_projects(runner, _host("ws-a")) == {"prj_a"}
    assert offered_projects(runner, _host("ws-a", "ws-x")) == {"prj_a", "prj_b"}


def test_a_project_with_one_unflagged_binding_is_offered() -> None:
    runner = _runner((RunnerBinding("alpha", "ws-x", 1), _A), None, _SERVED[:1])

    assert offered_projects(runner, _host("ws-a")) == {"prj_a"}


def test_an_unrostered_subscription_offers_nothing() -> None:
    assert offered_projects(_runner((_A,), "other", _SERVED), _host("ws-a")) == frozenset()


def test_an_unrostered_subscription_without_bindings_offers_nothing() -> None:
    assert offered_projects(_runner(None, "other", _SERVED), _host()) == frozenset()


def test_project_keeps_the_entry_it_names_by_entry_or_by_resolved_id() -> None:
    runner = _runner((_A, _X), None, _SERVED)
    host = _host("ws-a", "ws-x")

    assert offered_projects(runner, host, project="beta") == {"prj_b"}
    assert offered_projects(runner, host, project="prj_b") == {"prj_b"}
    assert offered_projects(runner, host, project="gamma") == frozenset()
    assert offered_projects(runner, host, project="old") == frozenset()


def test_project_cannot_widen_what_a_flag_withholds() -> None:
    runner = _runner((_X,), None, _SERVED)

    assert offered_projects(runner, _host("ws-a"), project="beta") == frozenset()


def test_a_runner_is_claimable_on_what_the_peek_offers_unless_a_runner_level_bar_stands() -> None:
    runner = _runner((_A, _X), "max", _SERVED)
    host = _host("ws-a")

    assert claimable_projects(runner, host) == offered_projects(runner, host) == {"prj_a"}
    for barred in (
        replace(runner, hub_paused=True),
        replace(runner, retired=True, retired_at=_AT, retired_by="op"),
        replace(runner, registered_at=None),
        replace(runner, version_refusal=VersionRefusalReason.TOO_OLD),
    ):
        assert claimable_projects(barred, host) == frozenset()
