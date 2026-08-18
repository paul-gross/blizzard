"""``blizzard hub analytics events`` against the real router (blizzard#257 Phase 2,
component tier): ``httpx.get``/``httpx.stream`` relayed to the app's own ``TestClient``,
so the route, the filters, and the auth triad's own detail strings all genuinely run."""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner, Result

import blizzard.hub.cli as hub_cli
from blizzard.auth_core import Role
from blizzard.hub.cli import hub as hub_group
from tests.support import HubHarness, seed_session, seed_user
from tests.test_analytics_events_api import _cookie, _seeded_hub

pytestmark = pytest.mark.component

_HUB_URL = "http://hub.local:8421"


def _relay(hub: HubHarness, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the CLI's module-level ``httpx`` calls onward to the real, wired app —
    carrying the same session cookie a logged-in operator's local session store would
    attach, so the real auth stack runs too."""
    headers = _cookie(token)

    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> httpx.Response:
        return hub.client.get(url, params=params, headers=headers)

    @contextlib.contextmanager
    def fake_stream(
        method: str, url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object
    ) -> Iterator[httpx.Response]:
        with hub.client.stream(method, url, params=params, headers=headers) as resp:
            yield resp

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    monkeypatch.setattr(hub_cli.httpx, "stream", fake_stream)


@contextlib.contextmanager
def _local_timezone(tz: str) -> Iterator[None]:
    """The dev/CI box's own local timezone is unspecified — pin it so a naive
    ``--since``/``--until`` (D6) converts predictably against the fixture's UTC stamps."""
    original = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(hub_group, ["analytics", "events", *args], env={"BZ_HUB_URL": _HUB_URL})


def test_the_default_table_reads_the_real_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)
    _relay(hub, token, monkeypatch)

    result = _invoke()

    assert result.exit_code == 0, result.output
    assert "src/a.py" in result.output
    assert "wf-commit" in result.output


def test_each_filter_flag_round_trips_to_a_filtered_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)
    _relay(hub, token, monkeypatch)

    by_kind = _invoke("--kind", "skill_invocation", "--json")
    assert by_kind.exit_code == 0, by_kind.output
    assert [e["subject"] for e in json.loads(by_kind.output)["events"]] == ["wf-commit"]

    by_tool = _invoke("--tool", "Agent", "--json")
    assert [e["subject"] for e in json.loads(by_tool.output)["events"]] == ["explorer"]

    by_prefix = _invoke("--subject-prefix", "src/", "--json")
    assert [e["subject"] for e in json.loads(by_prefix.output)["events"]] == ["src/a.py"]

    by_node = _invoke("--node", "nd_build", "--json")
    assert len(json.loads(by_node.output)["events"]) == 3

    with _local_timezone("UTC"):
        by_window = _invoke("--since", "2026-08-12T09:30:00", "--until", "2026-08-12T10:30:00", "--json")
    assert [e["subject"] for e in json.loads(by_window.output)["events"]] == ["wf-commit"]

    by_stale_version = _invoke("--extractor-version", "stale-version", "--json")
    assert json.loads(by_stale_version.output)["events"] == []


def test_ndjson_streams_every_line_unmodified_and_untruncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)
    _relay(hub, token, monkeypatch)
    paged = _invoke("--json")
    expected = json.loads(paged.output)["events"]

    streamed = _invoke("--ndjson")

    assert streamed.exit_code == 0, streamed.output
    lines = [json.loads(line) for line in streamed.output.strip().splitlines()]
    assert lines == expected
    assert len(lines) == 3


def test_the_auth_triad_returns_the_apis_own_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.support import build_hub

    hub = build_hub(tmp_path, auth_mode="oauth")

    def relay_anonymous(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object):
        return hub.client.get(url, params=params)

    monkeypatch.setattr(hub_cli.httpx, "get", relay_anonymous)
    anon = _invoke()
    assert anon.exit_code != 0
    assert "blizzard hub login" in anon.output

    guest = seed_user(hub, username="grace", role=Role.GUEST)
    guest_token = seed_session(hub, guest)
    _relay(hub, guest_token, monkeypatch)
    refused = _invoke()
    assert refused.exit_code != 0
    assert "transcript:read" in refused.output
