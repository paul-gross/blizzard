"""A runner's declining report: parsed off the registration body, stored whole on every registration, and served back
on both runner views — ``None``, ``[]`` and several reasons alike, a report superseding the one before."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.api import runners as runners_api
from blizzard.hub.domain.runners.registration import RunnerDeclining
from blizzard.wire.runner import RunnerRegistrationRequest
from tests.support import HubHarness, build_hub, seed_runner

pytestmark = pytest.mark.component

_RUNNER = "runner-a"
_RESET = datetime(2026, 10, 22, 14, tzinfo=UTC)
_FLOOR = RunnerDeclining("floor", "exhausted", "anthropic", _RESET)
_PACE = RunnerDeclining("pace", "7d 86% spent, 71% elapsed")


def _register(hub: HubHarness, declining: tuple[RunnerDeclining, ...] | None) -> None:
    held = hub.services.registry.get_runner(_RUNNER)
    assert held is not None
    hub.services.fleet.register(held, "w1", declining=declining)


def _held(hub: HubHarness) -> tuple[RunnerDeclining, ...]:
    held = hub.services.registry.get_runner(_RUNNER)
    assert held is not None
    return held.declining


def _hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    seed_runner(hub, _RUNNER, register=False)
    return hub


def test_the_request_parses_an_absent_report_an_empty_one_and_several_reasons() -> None:
    base = {"workspace_id": "w1"}
    reasons = [
        {"lever": "floor", "detail": "exhausted", "subscription": "anthropic", "until": "2026-10-22T14:00:00Z"},
        {"lever": "pace", "detail": "7d 86% spent"},
    ]

    assert RunnerRegistrationRequest.model_validate(base).declining is None
    assert RunnerRegistrationRequest.model_validate({**base, "declining": []}).declining == []
    parsed = RunnerRegistrationRequest.model_validate({**base, "declining": reasons}).declining
    assert parsed is not None
    assert [d.lever for d in parsed] == ["floor", "pace"]
    assert parsed[0].until == _RESET
    assert parsed[1].subscription is None


def test_the_request_admits_a_lever_it_has_no_name_for() -> None:
    body = {"workspace_id": "w1", "declining": [{"lever": "from-a-newer-runner", "detail": "d"}]}

    assert RunnerRegistrationRequest.model_validate(body).declining is not None


@pytest.mark.parametrize("sent", [None, ()], ids=["none", "empty"])
def test_a_report_with_no_reasons_reads_as_declining_nothing(tmp_path: Path, sent: tuple[()] | None) -> None:
    hub = _hub(tmp_path)

    _register(hub, sent)

    assert _held(hub) == ()


def test_several_reasons_round_trip_in_order_with_their_instants(tmp_path: Path) -> None:
    hub = _hub(tmp_path)

    _register(hub, (_FLOOR, _PACE))

    assert _held(hub) == (_FLOOR, _PACE)


def test_a_later_registration_supersedes_the_earlier_report_whole(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _register(hub, (_FLOOR, _PACE))

    _register(hub, (_PACE,))
    assert _held(hub) == (_PACE,)

    _register(hub, None)
    assert _held(hub) == ()


def test_both_views_serve_the_reasons(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _register(hub, (_FLOOR, _PACE))
    held = hub.services.registry.get_runner(_RUNNER)
    assert held is not None

    view = runners_api._declining_views(held)

    assert [(d.lever, d.detail, d.subscription, d.until) for d in view] == [
        ("floor", "exhausted", "anthropic", "2026-10-22T14:00:00+00:00"),
        ("pace", "7d 86% spent, 71% elapsed", None, None),
    ]
