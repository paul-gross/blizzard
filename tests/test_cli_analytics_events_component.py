"""``blizzard hub analytics events`` against the real router (component
tier): ``GET``s and streams relayed to the app's own ``TestClient``,
so the route, the filters, and the auth triad's own detail strings all genuinely run."""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from click.testing import Result

from blizzard.auth_core import Role
from blizzard.hub.cli import hub as hub_group
from tests.support import HubHarness, seed_session, seed_user
from tests.test_analytics_events_api import _cookie, _seeded_hub
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.component

_HUB_URL = "http://hub.local:8421"


def _relay(hub: HubHarness, token: str) -> Bound:
    """Route the CLI's requests onward to the real, wired app —
    carrying the same session cookie a logged-in operator's local session store would
    attach, so the real auth stack runs too."""
    headers = _cookie(token)

    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> httpx2.Response:
        return hub.client.get(url, params=params, headers=headers)

    @contextlib.contextmanager
    def fake_stream(
        method: str, url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object
    ) -> Iterator[httpx2.Response]:
        with hub.client.stream(method.upper(), url, params=params, headers=headers) as resp:
            yield resp

    return bind_stubs(get=fake_get, stream=fake_stream)


@contextlib.contextmanager
def _local_timezone(tz: str) -> Iterator[None]:
    """The dev/CI box's own local timezone is unspecified — pin it so a naive
    ``--since``/``--until`` converts predictably against the fixture's UTC stamps."""
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


def _invoke(bound: Bound, *args: str) -> Result:
    return bound.runner.invoke(hub_group, ["analytics", "events", *args], env={"BZ_HUB_URL": _HUB_URL})


def test_the_default_table_reads_the_real_events(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)
    bound = _relay(hub, token)

    result = _invoke(bound)

    assert result.exit_code == 0, result.output
    assert "src/a.py" in result.output
    assert "wf-commit" in result.output


def test_each_filter_flag_round_trips_to_a_filtered_result(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)
    bound = _relay(hub, token)

    by_kind = _invoke(bound, "--kind", "skill_invocation", "--json")
    assert by_kind.exit_code == 0, by_kind.output
    assert [e["subject"] for e in json.loads(by_kind.stdout)["events"]] == ["wf-commit"]

    by_tool = _invoke(bound, "--tool", "Agent", "--json")
    assert [e["subject"] for e in json.loads(by_tool.stdout)["events"]] == ["explorer"]

    by_prefix = _invoke(bound, "--subject-prefix", "src/", "--json")
    assert [e["subject"] for e in json.loads(by_prefix.stdout)["events"]] == ["src/a.py"]

    by_node = _invoke(bound, "--node", "nd_build", "--json")
    assert len(json.loads(by_node.stdout)["events"]) == 3

    with _local_timezone("UTC"):
        by_window = _invoke(bound, "--since", "2026-08-12T09:30:00", "--until", "2026-08-12T10:30:00", "--json")
    assert [e["subject"] for e in json.loads(by_window.stdout)["events"]] == ["wf-commit"]

    by_stale_version = _invoke(bound, "--extractor-version", "stale-version", "--json")
    assert json.loads(by_stale_version.stdout)["events"] == []


def test_ndjson_streams_every_line_unmodified_and_untruncated(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)
    bound = _relay(hub, token)
    paged = _invoke(bound, "--json")
    expected = json.loads(paged.stdout)["events"]

    streamed = _invoke(bound, "--ndjson")

    assert streamed.exit_code == 0, streamed.output
    lines = [json.loads(line) for line in streamed.stdout.strip().splitlines()]
    assert lines == expected
    assert len(lines) == 3


def test_the_auth_triad_returns_the_apis_own_detail(tmp_path: Path) -> None:
    from tests.support import build_hub

    hub = build_hub(tmp_path, auth_mode="oauth")

    def relay_anonymous(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object):
        return hub.client.get(url, params=params)

    anon = _invoke(bind_stubs(get=relay_anonymous))
    assert anon.exit_code != 0
    assert "blizzard hub login" in anon.output

    guest = seed_user(hub, username="grace", role=Role.GUEST)
    guest_token = seed_session(hub, guest)
    refused = _invoke(_relay(hub, guest_token))
    assert refused.exit_code != 0
    assert "transcript:read" in refused.output
