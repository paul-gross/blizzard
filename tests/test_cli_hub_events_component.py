"""``blizzard hub events`` against the real router (component tier): ``httpx.get`` relayed
to the app's own ``TestClient``, so the route, its filters, and the auth triad genuinely run."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner, Result

from blizzard.auth_core import Role
from blizzard.foundation.event_log import EventLogSeverity
from blizzard.hub.cli import hub as hub_group
from tests.support import HubHarness, build_hub, chunk_stores, seed_chunk, seed_graph, seed_session, seed_user

pytestmark = pytest.mark.component

_HUB_URL = "http://hub.local:8421"


def _relay(hub: HubHarness, monkeypatch: pytest.MonkeyPatch, token: str | None) -> None:
    headers = {"Cookie": f"bz_session={token}"} if token is not None else {}

    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> httpx.Response:
        return hub.client.get(url, params=params, headers=headers)

    monkeypatch.setattr(httpx, "get", fake_get)


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(hub_group, ["events", *args], env={"BZ_HUB_URL": _HUB_URL})


def _seeded_hub(tmp_path: Path) -> tuple[HubHarness, str, str]:
    """Three event-log rows across two chunks and runners; returns the hub, a session, and
    the local wall-clock instant between the first row and the later two."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    token = seed_session(hub, seed_user(hub, username="ada", role=Role.CONTRIBUTOR))
    store = chunk_stores(hub.engine, hub.clock)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
        for cid in ("ch_a", "ch_b"):
            seed_chunk(conn, cid, graph_id="gr_1", at=t0)
    rows: list[tuple[EventLogSeverity, str, str, str, str, int]] = [
        ("info", "attempt-abandoned", "r1", "ch_a", "abandoned", 1),
        ("warning", "attempt-failed", "r1", "ch_a", "retried", 10),
        ("critical", "worker-lost", "r2", "ch_b", "lost", 20),
    ]
    for severity, kind, runner, chunk, message, sec in rows:
        store.events.record_event(
            severity=severity,
            kind=kind,
            runner_id=runner,
            chunk_id=chunk,
            lease_id=None,
            node_name="build",
            message=message,
            detail={"n": sec},
            at=t0 + timedelta(seconds=sec),
        )
    between = (t0 + timedelta(seconds=5)).astimezone().replace(tzinfo=None)
    return hub, token, between.isoformat(timespec="seconds")


def _messages(result: Result) -> list[str]:
    assert result.exit_code == 0, result.output
    return [e["message"] for e in json.loads(result.output)["events"]]


def test_each_filter_matches_the_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, token, between = _seeded_hub(tmp_path)
    _relay(hub, monkeypatch, token)

    assert _messages(_invoke("--json")) == ["lost", "retried", "abandoned"]
    assert _messages(_invoke("--chunk", "ch_a", "--json")) == ["retried", "abandoned"]
    assert _messages(_invoke("--severity", "warning", "--json")) == ["retried"]
    assert _messages(_invoke("--runner", "r2", "--json")) == ["lost"]
    assert _messages(_invoke("--since", between, "--json")) == ["lost", "retried"]
    assert _messages(_invoke("--limit", "1", "--json")) == ["lost"]


def test_json_carries_detail_and_the_table_reads_the_real_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, token, _between = _seeded_hub(tmp_path)
    _relay(hub, monkeypatch, token)

    as_json = _invoke("--chunk", "ch_b", "--json")
    assert json.loads(as_json.output)["events"][0]["detail"] == {"n": 20}

    table = _invoke("--chunk", "ch_b")
    assert table.exit_code == 0, table.output
    assert "worker-lost" in table.output
    assert "runner=r2" in table.output


def test_the_auth_triad_returns_the_apis_own_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")

    _relay(hub, monkeypatch, None)
    anon = _invoke()
    assert anon.exit_code != 0
    assert "blizzard hub login" in anon.output

    pending = seed_user(hub, username="pat", role=Role.PENDING)
    _relay(hub, monkeypatch, seed_session(hub, pending))
    refused = _invoke()
    assert refused.exit_code != 0
    assert "fleet:view" in refused.output
