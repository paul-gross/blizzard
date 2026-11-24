"""Settings verbs use the selected daemon and condition writes on its revision."""

import json
from pathlib import Path

import httpx2
import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.host_config import decode_host_text
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
        return httpx2.Response(200, json={"revision": 7, "in_force": {}, "settings": []})

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
        return httpx2.Response(200, json={"revision": 3, "in_force": {}, "settings": []})

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
        return httpx2.Response(
            200,
            json={
                "revision": 2,
                "in_force": {},
                "settings": [{"key": "agents", "value": 3, "source": "entry", "lifetime": "pass"}],
            },
        )

    result = CliRunner().invoke(
        runner_group,
        ["settings", verb, "agents", "--runner-url", "http://runner.test"],
        obj=bind_transport(answer).collaborators,
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["value"] == 3


def test_get_key_reports_settings_resolution_failure() -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/host/runners":
            return httpx2.Response(
                200, json={"items": [{"name": "r", "runner_id": "rn_1", "state": "serving", "served": True}]}
            )
        assert request.url.path == "/runners/rn_1/api/settings"
        return httpx2.Response(
            200, json={"revision": 2, "in_force": {}, "settings": [], "settings_error": "cannot read .env"}
        )

    result = CliRunner().invoke(
        runner_group,
        ["settings", "get", "agents", "--runner-url", "http://runner.test"],
        obj=bind_transport(answer).collaborators,
    )
    assert result.exit_code != 0
    assert "settings unavailable" in result.output
    assert "cannot read .env" in result.output
    assert "unknown setting" not in result.output


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


def test_host_write_reaches_the_host_record_without_selecting_a_runner() -> None:
    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.url.path == "/api/host/settings"
        if request.method == "PATCH":
            assert request.headers["If-Match"] == "4"
            assert json.loads(request.content) == {"set": {"tick_seconds": 2}, "unset": []}
        return httpx2.Response(200, json={"revision": 4, "in_force": {}, "settings": []})

    result = CliRunner().invoke(
        runner_group,
        ["settings", "set", "tick_seconds", "2", "--host", "--runner-url", "http://runner.test"],
        obj=bind_transport(answer).collaborators,
    )
    assert result.exit_code == 0, result.output
    assert [r.method for r in requests] == ["GET", "PATCH"]


@pytest.mark.parametrize("verb", [["get"], ["set", "tick_seconds", "2"], ["unset", "tick_seconds"], ["changes"]])
def test_host_and_runner_are_mutually_exclusive(verb: list[str]) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"no request is sent: {request.url}")

    result = CliRunner().invoke(
        runner_group,
        ["settings", *verb, "--host", "--runner", "r", "--runner-url", "http://runner.test"],
        obj=bind_transport(answer).collaborators,
    )
    assert result.exit_code == 2
    assert "--host and --runner are mutually exclusive" in result.output


@pytest.mark.parametrize(("flags", "query"), [([], ""), (["--dry-run"], "dry_run=true")])
def test_apply_posts_the_decoded_document_to_the_host(tmp_path: Path, flags: list[str], query: str) -> None:
    file = tmp_path / "host.toml"
    file.write_text('tick_seconds = 3\n[tracing]\nbatch_limit = 4\n[[runner]]\nid = "rn_1"\nagents = 2\n')
    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"dry_run": bool(query), "apply_id": None, "outcomes": []})

    bound = bind_transport(answer)
    result = CliRunner().invoke(
        runner_group,
        ["settings", "apply", str(file), *flags, "--runner-url", "http://runner.test"],
        obj=bound.collaborators,
    )
    assert result.exit_code == 0, result.output
    [request] = requests
    assert (request.method, request.url.path, request.url.query.decode()) == ("POST", "/api/host/settings/apply", query)
    assert json.loads(request.content) == {
        "tick_seconds": 3,
        "tracing": {"batch_limit": 4},
        "runner": [{"id": "rn_1", "agents": 2}],
    }


def test_apply_refuses_a_file_that_is_not_toml(tmp_path: Path) -> None:
    file = tmp_path / "host.toml"
    file.write_text("tick_seconds = = 3")
    bound = bind_transport(lambda request: httpx2.Response(500))
    result = CliRunner().invoke(
        runner_group, ["settings", "apply", str(file), "--runner-url", "http://runner.test"], obj=bound.collaborators
    )
    assert result.exit_code == 2
    assert "not valid TOML" in result.output


def test_export_prints_toml_that_decodes_to_the_routes_document() -> None:
    document = {
        "tracing": {"batch_limit": 10, "worker_program_services": {"claude": "svc"}},
        "workspace": [{"id": "ws_1", "envs": ["e1"]}],
        "runner": [{"id": "rn_1", "agents": 1, "harness": {"autonomy": "full"}}],
    }

    def answer(request: httpx2.Request) -> httpx2.Response:
        assert (request.method, request.url.path) == ("GET", "/api/host/settings/export")
        return httpx2.Response(200, json=document)

    bound = bind_transport(answer)
    result = CliRunner().invoke(
        runner_group, ["settings", "export", "--runner-url", "http://runner.test"], obj=bound.collaborators
    )
    assert result.exit_code == 0, result.output
    assert decode_host_text(result.output, what="export") == document
