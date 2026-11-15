"""Settings verbs use the selected daemon and condition writes on its revision."""

import json

import httpx2
import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from tests.support.worker_http import bind_transport


@pytest.mark.parametrize(
    ("verb", "arguments", "body"),
    [
        (
            "set",
            ["models", '{ name = "opus", choices = [1, 2] }'],
            {"set": {"models": {"name": "opus", "choices": [1, 2]}}, "unset": []},
        ),
        ("unset", ["agents"], {"set": {}, "unset": ["agents"]}),
    ],
)
def test_write_sends_observed_revision(verb: str, arguments: list[str], body: object) -> None:
    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/api/host/runners":
            return httpx2.Response(
                200, json={"items": [{"name": "renamed", "runner_id": "rn_1", "state": "serving", "served": True}]}
            )
        assert request.url.path == "/runners/rn_1/api/settings"
        if request.method == "PATCH":
            assert request.headers["If-Match"] == "7"
            assert json.loads(request.content) == body
        return httpx2.Response(200, json={"revision": 7, "settings": []})

    bound = bind_transport(answer)
    result = CliRunner().invoke(
        runner_group,
        ["settings", verb, *arguments, "--runner", "renamed", "--runner-url", "http://runner.test"],
        obj=bound.collaborators,
    )
    assert result.exit_code == 0, result.output
    assert [r.method for r in requests] == ["GET", "GET", "PATCH"]


def test_conflict_is_reported_without_retry() -> None:
    calls: list[str] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.method)
        if request.url.path == "/api/host/runners":
            return httpx2.Response(
                200, json={"items": [{"name": "r", "runner_id": "rn_1", "state": "serving", "served": True}]}
            )
        if request.method == "PATCH":
            return httpx2.Response(409, json={"detail": {"code": "revision_conflict", "message": "stale revision"}})
        return httpx2.Response(200, json={"revision": 3, "settings": []})

    result = CliRunner().invoke(
        runner_group,
        ["settings", "unset", "agents", "--runner-url", "http://runner.test"],
        obj=bind_transport(answer).collaborators,
    )
    assert result.exit_code != 0
    assert "stale revision" in result.output
    assert calls == ["GET", "GET", "PATCH"]


@pytest.mark.parametrize("verb", ["get", "show", "read"])
def test_get_aliases_read_one_key(verb: str) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/host/runners":
            return httpx2.Response(
                200, json={"items": [{"name": "r", "runner_id": "rn_1", "state": "serving", "served": True}]}
            )
        return httpx2.Response(200, json={"revision": 2, "settings": [{"key": "agents", "value": 3}]})

    result = CliRunner().invoke(
        runner_group,
        ["settings", verb, "agents", "--runner-url", "http://runner.test"],
        obj=bind_transport(answer).collaborators,
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["value"] == 3


def test_ambiguous_selection_does_not_read_settings() -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/host/runners"
        return httpx2.Response(
            200,
            json={
                "items": [
                    {"name": "a", "runner_id": "rn_1", "state": "serving", "served": True},
                    {"name": "b", "runner_id": "rn_2", "state": "serving", "served": True},
                ]
            },
        )

    result = CliRunner().invoke(
        runner_group,
        ["settings", "get", "--runner-url", "http://runner.test"],
        obj=bind_transport(answer).collaborators,
    )
    assert result.exit_code != 0
    assert "several runners" in result.output
