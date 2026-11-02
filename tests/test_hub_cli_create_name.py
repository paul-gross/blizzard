"""``blizzard hub project|repo|source create`` (unit tier) — without ``--name`` the request carries no ``name``: the
hub decides the default, the CLI sends only what the operator typed."""

from __future__ import annotations

import pytest

from blizzard.hub.cli import hub as hub_group
from tests.worker_http import bind_stubs


class _Response:
    status_code = 201

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


_CREATES: list[tuple[list[str], dict[str, object]]] = [
    (["project", "create", "alpha"], {"slug": "alpha", "project_id": "pr_1"}),
    (
        [
            "repo",
            "create",
            "alpha",
            "--forge",
            "github",
            "--owner",
            "o",
            "--repo",
            "r",
            "--base-branch",
            "main",
            "--secret",
            "s",
        ],
        {"slug": "alpha", "repository_id": "re_1"},
    ),
    (
        ["source", "create", "alpha", "--provider", "github", "--locator", "o/r", "--secret", "s"],
        {"slug": "alpha", "work_source_id": "ws_1"},
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("argv", "reply"), _CREATES, ids=["project", "repo", "source"])
def test_create_without_name_omits_name_on_the_wire(argv: list[str], reply: dict[str, object]) -> None:
    bodies: list[dict[str, object]] = []

    def fake_post(url: str, *, json: dict[str, object], **_: object) -> _Response:
        bodies.append(json)
        return _Response(reply)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, argv, env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert len(bodies) == 1
    assert "name" not in bodies[0]
    assert bodies[0]["slug"] == "alpha"


@pytest.mark.unit
@pytest.mark.parametrize(("argv", "reply"), _CREATES, ids=["project", "repo", "source"])
def test_create_with_name_sends_it(argv: list[str], reply: dict[str, object]) -> None:
    bodies: list[dict[str, object]] = []

    def fake_post(url: str, *, json: dict[str, object], **_: object) -> _Response:
        bodies.append(json)
        return _Response(reply)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, [*argv, "--name", "Alpha"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert bodies[0]["name"] == "Alpha"
