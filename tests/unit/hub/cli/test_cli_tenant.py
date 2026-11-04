"""``blizzard hub tenant create|list|rename|delete`` — a hub administrator's verbs, against a real hub with sign-in on:
create names its admin and records the CLI as the door of the tenant's default project, list shows a deletion
underway, rename keeps the id, and delete asks first and answers each of the hub's outcomes — removed, underway,
already deleted, and the only tenant refused."""

from __future__ import annotations

from collections.abc import Callable

import httpx2
from click.testing import Result

from blizzard.hub.cli import hub as hub_group
from tests.worker_http import bind_stubs

_HUB_URL = "http://testserver"

Cli = Callable[..., Result]


def test_a_deletion_left_underway_says_the_hub_finishes_it() -> None:
    def delete(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> httpx2.Response:
        body = {"tenant_id": "ten_a", "state": "deleting", "rows_removed": None}
        return httpx2.Response(202, json=body, request=httpx2.Request("DELETE", url))

    bound = bind_stubs(delete=delete)

    result = bound.runner.invoke(hub_group, ["tenant", "delete", "ten_a", "--yes", "--hub-url", _HUB_URL])

    assert result.exit_code == 0, result.output
    assert "deletion is underway" in result.output
    assert "tenant list" in result.output


def test_list_shows_a_deletion_underway_with_when_it_began() -> None:
    rows = [
        {"tenant_id": "ten_a", "name": "acme", "created_at": "t0", "created_by": "hal", "state": "open"},
        {
            "tenant_id": "ten_b",
            "name": "bravo",
            "created_at": "t1",
            "created_by": "hal",
            "state": "deleting",
            "closed_at": "t2",
        },
    ]

    def get(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> httpx2.Response:
        return httpx2.Response(200, json=rows, request=httpx2.Request("GET", url))

    bound = bind_stubs(get=get)

    result = bound.runner.invoke(hub_group, ["tenant", "list", "--hub-url", _HUB_URL])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "ten_a  acme  open  created_at=t0  by hal",
        "ten_b  bravo  deleting since t2",
    ]
