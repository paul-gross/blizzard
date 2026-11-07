"""Tenant list and delete render an underway deletion using stubbed hub responses."""

from __future__ import annotations

from collections.abc import Callable

import httpx2
from click.testing import Result

from blizzard.hub.cli import hub as hub_group
from blizzard.wire.problem_types_admin import TENANT_DELETED
from tests.support.worker_http import bind_stubs

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


def test_a_410_on_delete_reads_as_deleted_typed_or_not() -> None:
    typed = {"type": TENANT_DELETED, "title": "Tenant deleted", "detail": "tenant deleted", "status": 410}

    def delete_typed(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> httpx2.Response:
        return httpx2.Response(410, json=typed, request=httpx2.Request("DELETE", url))

    def delete_untyped(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> httpx2.Response:
        return httpx2.Response(410, json={"detail": "tenant deleted"}, request=httpx2.Request("DELETE", url))

    args = ["tenant", "delete", "ten_a", "--yes", "--hub-url", _HUB_URL]
    said = bind_stubs(delete=delete_typed).runner.invoke(hub_group, args)
    refused = bind_stubs(delete=delete_untyped).runner.invoke(hub_group, args)

    assert said.exit_code == 0, said.output
    assert said.output.strip() == "tenant ten_a was already deleted"
    assert refused.exit_code == 0, refused.output
    assert refused.output.strip() == "tenant ten_a was already deleted"
