"""``blizzard hub project|repo|source create`` (unit tier) — without ``--name`` the request carries no ``name``: the
hub decides the default, the CLI sends only what the operator typed."""

from __future__ import annotations

import pytest

from blizzard.hub.cli import hub as hub_group
from blizzard.wire.project import ProjectView
from blizzard.wire.repository import RepositorySummary
from blizzard.wire.work_source import WorkSourceSummary
from tests.support.worker_http import bind_stubs


class _Response:
    status_code = 201

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


_PROJECT = ProjectView(
    project_id="pr_1",
    slug="alpha",
    name="alpha",
    description="",
    color="blue",
    revision=1,
    created_at="t0",
).model_dump(mode="json")
_REPOSITORY = RepositorySummary(
    repository_id="re_1",
    slug="alpha",
    name="alpha",
    forge_id="fo_1",
    forge_slug="github",
    forge_name="GitHub",
    owner="o",
    repo="r",
    base_branch="main",
    secret_name="s",
    landing_policy="merge-on-checks",
    revision=1,
    created_at="t0",
    created_by="operator",
).model_dump(mode="json")
_SOURCE = WorkSourceSummary(
    work_source_id="ws_1",
    slug="alpha",
    name="alpha",
    annotate=False,
    edit=False,
    provider="github",
    locator="o/r",
).model_dump(mode="json")

_CREATES: list[tuple[list[str], dict[str, object]]] = [
    (["project", "create", "alpha"], _PROJECT),
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
        _REPOSITORY,
    ),
    (
        ["source", "create", "alpha", "--provider", "github", "--locator", "o/r", "--secret", "s"],
        _SOURCE,
    ),
]


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
