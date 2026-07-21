"""``blizzard hub status`` — the per-chunk cost column and fleet total (issue #60).

A pure client of the hub API: ``GET /chunks`` + ``GET /runners`` + ``GET /questions``
+ ``GET /spend``, all through the shared ``_request`` seam (issue #104) — this file
stubs ``httpx.get`` (the same monkeypatch seam every other CLI unit test uses) with
canned responses keyed by the full URL, so it proves the CLI's own rendering — the
per-chunk cost column, the fleet total, and the lower-bound PARTIAL marker — without a
running hub.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

import blizzard.hub.cli as hub_cli
from blizzard.hub.cli import DEFAULT_HUB_URL
from blizzard.hub.cli import hub as hub_group

pytestmark = pytest.mark.component


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


def _install(monkeypatch: pytest.MonkeyPatch, responses: dict[str, object]) -> None:
    """Key ``responses`` by full URL (``DEFAULT_HUB_URL`` + path) — what ``_request``
    actually calls ``httpx.get`` with."""

    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse(responses[url])

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)


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
        f"{DEFAULT_HUB_URL}/api/chunks": [
            {"chunk_id": "ch_1", "status": "running", "current_node_id": "nd_1", "cost": chunk_cost},
        ],
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
) -> dict:
    return {
        "runner_id": "r1",
        "workspace_id": "ws1",
        "online": True,
        "hub_paused": hub_paused,
        "locally_paused": locally_paused,
        "locally_paused_by": locally_paused_by,
        "locally_paused_reason": locally_paused_reason,
    }


def test_status_renders_a_per_chunk_cost_column_and_the_fleet_total(monkeypatch: pytest.MonkeyPatch) -> None:
    cost = _cost(0.42, partial=False)
    _install(monkeypatch, _responses(cost, cost))

    result = CliRunner().invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert "ch_1" in result.output
    assert "$0.42" in result.output
    assert "fleet spend" in result.output.lower()
    # Exactly the chunk row's figure and the fleet total's — no stray partial marker.
    assert "~" not in result.output


def test_status_marks_a_partial_total_on_both_the_chunk_row_and_the_fleet_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cost = _cost(0.10, partial=True)
    _install(monkeypatch, _responses(cost, cost))

    result = CliRunner().invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    # The chunk row and the fleet total both carry the lower-bound marker.
    assert result.output.count("~$0.10") == 2


def test_status_names_a_ceiling_pause_reason_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A runner-ceiling escalation (issue #61) is distinguishable from a manual pause on
    ``blizzard hub status`` — the composed ceiling+spend reason rides inline rather than
    the bare ``[paused: local]`` a manual pause renders."""
    cost = _cost(0.0, partial=False)
    reason = "spend ceiling $5.00 reached over the trailing 24h (spend $7.00)"
    runners = [_runner(locally_paused=True, locally_paused_by="runner-ceiling", locally_paused_reason=reason)]
    _install(monkeypatch, _responses(cost, cost, runners))

    result = CliRunner().invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert f"[paused: local — {reason}]" in result.output


def test_status_renders_a_manual_pause_bare_with_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manual `blizzard runner pause` carries no reason — the status line stays bare
    rather than showing a stale or fabricated cause."""
    cost = _cost(0.0, partial=False)
    runners = [_runner(locally_paused=True, locally_paused_by="operator", locally_paused_reason=None)]
    _install(monkeypatch, _responses(cost, cost, runners))

    result = CliRunner().invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert "[paused: local]" in result.output
    assert "—" not in result.output


def test_status_names_both_brakes_with_the_local_reason_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both brakes on at once still name which is which, and the local one's reason still
    rides alongside it (issue #43's naming, extended by issue #61's reason)."""
    cost = _cost(0.0, partial=False)
    reason = "spend ceiling $5.00 reached over the trailing 24h (spend $7.00)"
    runners = [
        _runner(hub_paused=True, locally_paused=True, locally_paused_by="runner-ceiling", locally_paused_reason=reason)
    ]
    _install(monkeypatch, _responses(cost, cost, runners))

    result = CliRunner().invoke(hub_group, ["status"])

    assert result.exit_code == 0, result.output
    assert f"[paused: hub+local — {reason}]" in result.output
