"""``permits`` over a stubbed hub: its container-owned reader is finalized however the verb ends (component tier)."""

# exemplar: bzh:dependency-injection — the invoker's carrier is what the verb's container borrows.

from __future__ import annotations

import httpx2

from blizzard.hub.cli.context import DEFAULT_HUB_URL
from exemplar.hub.cli.composition import hub
from tests.support.worker_http import bind_transport

_PERMIT = {
    "permit_id": "pm_1",
    "holder": "ada",
    "capability": "deploy",
    "state": "requested",
    "verbs": ["grant"],
    "requested_at": "2026-11-01T00:00:00+00:00",
    "requested_by": "us_1",
}


def test_permits_lists_what_the_hub_holds_then_closes_its_reader() -> None:
    requested: list[str] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(200, json={"permits": [_PERMIT], "next_cursor": None})

    bound = bind_transport(answer)
    result = bound.runner.invoke(hub, ["permits", "--project", "acme"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "permits (1):\n  pm_1  ada  deploy\n"
    assert result.stderr == "read 1 permit(s)\n"
    assert requested == [f"{DEFAULT_HUB_URL}/api/permits?project=acme&limit=1000"]
    assert [client.is_closed for client in bound.built] == [True]


def test_permits_closes_its_reader_when_the_hub_refuses() -> None:
    bound = bind_transport(lambda request: httpx2.Response(500, json={"detail": "the hub fell over"}))

    result = bound.runner.invoke(hub, ["permits"])

    assert result.exit_code == 1
    assert result.stderr == "read 0 permit(s)\nError: the hub fell over\n"
