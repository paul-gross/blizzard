"""``blizzard hub status`` — the per-chunk cost column and fleet total.

Stubs the client's GET with canned responses keyed by URL, proving the CLI's own
rendering — the per-chunk cost column, the fleet total, and the ``~``-estimated/
``+``-partial markers — without a running hub.
"""

from __future__ import annotations

import json
from typing import cast

import pytest

from blizzard.hub.cli import hub as hub_group
from blizzard.hub.cli.context import DEFAULT_HUB_URL
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.component


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        # the `_check` reads `.status_code` unconditionally — every response
        # this file installs is a plain 200.
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


def _install(responses: dict[str, object]) -> Bound:
    """Key ``responses`` by full URL (``DEFAULT_HUB_URL`` + path) — what ``_request``
    actually requests."""

    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse(responses[url])

    return bind_stubs(get=fake_get)


def _cost(cost_usd: float, *, partial: bool) -> dict:
    return {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_create_tokens": 0,
        "cost_usd": cost_usd,
        "cost_partial": partial,
    }


def _responses(chunk_cost: dict, fleet_cost: dict, runners: list[dict] | None = None) -> dict[str, object]:
    return {
        f"{DEFAULT_HUB_URL}/api/chunks": {
            "chunks": [
                {"chunk_id": "ch_1", "status": "running", "current_node_id": "nd_1", "cost": chunk_cost},
            ],
            "next_cursor": None,
        },
        f"{DEFAULT_HUB_URL}/api/runners": {"runners": runners or []},
        f"{DEFAULT_HUB_URL}/api/questions": [],
        f"{DEFAULT_HUB_URL}/api/spend": {"since": "1970-01-01T00:00:00+00:00", **fleet_cost},
    }


def _runner(
    *,
    hub_paused: bool = False,
    locally_paused: bool = False,
    locally_paused_by: str | None = None,
    locally_paused_reason: str | None = None,
    online: bool = True,
    declining: list[dict] | None = None,
) -> dict:
    return {
        "runner_id": "r1",
        "workspace_id": "ws1",
        "online": online,
        "hub_paused": hub_paused,
        "locally_paused": locally_paused,
        "locally_paused_by": locally_paused_by,
        "locally_paused_reason": locally_paused_reason,
        "declining": declining or [],
    }


def test_status_renders_a_per_chunk_cost_column_and_the_fleet_total() -> None:
    cost = _cost(0.42, partial=False)
    bound = _install(_responses(cost, cost))

    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert "ch_1" in result.output
    assert "$0.42" in result.output
    assert "fleet spend" in result.output.lower()
    assert result.output.count("$0.42") == 2
    assert "~$" not in result.output
    assert "$0.42+" not in result.output


def test_status_marks_a_partial_total_on_both_the_chunk_row_and_the_fleet_total() -> None:
    cost = _cost(0.10, partial=True)
    bound = _install(_responses(cost, cost))

    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    # The chunk row and the fleet total both carry the lower-bound marker.
    assert result.output.count("$0.10+") == 2


def test_status_folds_the_estimate_into_the_chunk_row_and_the_fleet_total() -> None:
    cost = {**_cost(0.0, partial=False), "estimated_cost_usd": 0.31}
    bound = _install(_responses(cost, cost))

    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    # A subscription-only chunk folds its estimate into one `~`-marked figure, never a bare $0.00.
    assert result.output.count("~$0.31") == 2
    assert "est." not in result.output


def test_status_renders_no_estimate_where_none_was_reported() -> None:
    cost = {**_cost(0.42, partial=False), "estimated_cost_usd": None}
    bound = _install(_responses(cost, cost))

    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert result.output.count("$0.42") == 2
    assert "~$" not in result.output


def test_status_names_a_ceiling_pause_reason_inline() -> None:
    """A runner-ceiling escalation is distinguishable from a manual pause on
    ``blizzard hub status`` — the composed ceiling+spend reason rides inline rather than
    the bare ``[paused: local]`` a manual pause renders."""
    cost = _cost(0.0, partial=False)
    reason = "spend ceiling $5.00 reached over the trailing 24h (spend $7.00)"
    runners = [_runner(locally_paused=True, locally_paused_by="runner-ceiling", locally_paused_reason=reason)]
    bound = _install(_responses(cost, cost, runners))

    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert f"[paused: local — {reason}]" in result.output


def test_status_renders_a_manual_pause_bare_with_no_reason() -> None:
    """A manual `blizzard runner pause` carries no reason — the status line stays bare
    rather than showing a stale or fabricated cause."""
    cost = _cost(0.0, partial=False)
    runners = [_runner(locally_paused=True, locally_paused_by="operator", locally_paused_reason=None)]
    bound = _install(_responses(cost, cost, runners))

    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert "[paused: local]" in result.output
    assert "—" not in result.output


def test_status_names_a_hub_pause_with_no_local_brake() -> None:
    """A fleet-side pause names itself ``hub``, distinct from a local one — collapsing both to
    a bare ``paused`` would hide which verb clears it."""
    cost = _cost(0.0, partial=False)
    runners = [_runner(hub_paused=True)]
    bound = _install(_responses(cost, cost, runners))

    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert "[paused: hub]" in result.output


def test_status_names_both_brakes_with_the_local_reason_inline() -> None:
    """Both brakes on at once still name which is which, and the local one's reason still
    rides alongside it."""
    cost = _cost(0.0, partial=False)
    reason = "spend ceiling $5.00 reached over the trailing 24h (spend $7.00)"
    runners = [
        _runner(hub_paused=True, locally_paused=True, locally_paused_by="runner-ceiling", locally_paused_reason=reason)
    ]
    bound = _install(_responses(cost, cost, runners))

    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert f"[paused: hub+local — {reason}]" in result.output


def test_status_drains_every_page_of_chunks() -> None:
    cost = _cost(0.0, partial=False)
    page_1 = {
        "chunks": [
            {"chunk_id": "ch_1", "status": "running", "current_node_id": "nd_1", "cost": cost},
        ],
        "next_cursor": "cursor-1",
    }
    page_2 = {
        "chunks": [
            {"chunk_id": "ch_2", "status": "running", "current_node_id": "nd_1", "cost": cost},
        ],
        "next_cursor": None,
    }
    responses = _responses(cost, cost)

    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> _FakeResponse:
        if url == f"{DEFAULT_HUB_URL}/api/chunks":
            if params is None or params.get("cursor") is None:
                return _FakeResponse(page_1)
            return _FakeResponse(page_2)
        return _FakeResponse(responses[url])

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert "ch_1" in result.output
    assert "ch_2" in result.output


def test_status_json_keeps_raw_fields_across_pages_and_old_server_defaults() -> None:
    first = {"chunk_id": "one", "extra": {"untouched": [1, None]}}
    second = {"chunk_id": "two", "extra": False}
    responses = _responses({}, {})
    responses[f"{DEFAULT_HUB_URL}/api/runners"] = {"server_extra": [1, 2]}
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> _FakeResponse:
        if url.endswith("/api/chunks"):
            calls.append(dict(params or {}))
            return _FakeResponse(
                {"chunks": [second]} if params and params.get("cursor") else {"chunks": [first], "next_cursor": "c"}
            )
        return _FakeResponse(responses[url])

    result = bind_stubs(get=fake_get).runner.invoke(hub_group, ["status", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "chunks": [first, second],
        "runners": {"server_extra": [1, 2]},
        "questions": [],
        "spend": responses[f"{DEFAULT_HUB_URL}/api/spend"],
    }
    assert calls == [{"limit": "1000"}, {"limit": "1000", "cursor": "c"}]


def test_status_marks_a_blocked_chunk_naming_the_prerequisite() -> None:
    """`hub status` shares `ChunkLine` with `hub chunk list`, so the blocked marking
    is proven once here to cover both."""
    cost = _cost(0.0, partial=False)
    responses = _responses(cost, cost)
    envelope = cast("dict", responses[f"{DEFAULT_HUB_URL}/api/chunks"])
    envelope["chunks"][0]["blocked"] = {"prerequisite_chunk_id": "ch_prereq"}
    bound = _install(responses)

    result = bound.runner.invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert "[blocked on ch_prereq]" in result.output


_FLOOR = {"lever": "floor", "detail": "exhausted", "subscription": "anthropic", "until": "2026-10-22T14:00:00Z"}
_PACE = {"lever": "pace", "detail": "7d 86% spent, 71% elapsed", "subscription": None, "until": None}


def _status_output(runner: dict) -> str:
    cost = _cost(0.0, partial=False)
    result = _install(_responses(cost, cost, [runner])).runner.invoke(hub_group, ["status"])
    assert result.exit_code == 0, result.output
    return result.output


def test_status_names_the_floor_an_online_unbraked_runner_is_held_by() -> None:
    assert "[declining: floor — anthropic exhausted, resets 14:00Z]" in _status_output(_runner(declining=[_FLOOR]))


def test_status_shows_the_first_reason_in_the_gates_order_and_words_a_lever_generically() -> None:
    output = _status_output(_runner(declining=[_PACE, _FLOOR]))

    assert "[declining: pace — 7d 86% spent, 71% elapsed]" in output
    assert "floor" not in output


def test_status_prints_the_brake_in_place_of_the_declining_segment() -> None:
    output = _status_output(_runner(hub_paused=True, declining=[_FLOOR]))

    assert "[paused: hub]" in output
    assert "declining" not in output


def test_status_prints_no_declining_segment_for_an_offline_runner() -> None:
    assert "declining" not in _status_output(_runner(online=False, declining=[_FLOOR]))
