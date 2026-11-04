"""``blizzard hub runner add|list|get`` — the rendering half with the client stubbed (unit
tier), and the CLI driven against a real hub app (component tier), every by-id verb included."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from blizzard.hub.cli import hub as hub_group
from tests.support_hub_cli_runner import ENV, HUB_URL
from tests.worker_http import Bound, bind_stubs

_ADDED = {"runner_id": "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6T", "runner_name": "r-claude", "token": "sekrit-token"}


def _respond(status_code: int, payload: object) -> httpx2.Response:
    return httpx2.Response(status_code, json=payload, request=httpx2.Request("GET", HUB_URL))


def _recording(calls: list[dict[str, Any]], response: httpx2.Response) -> Callable[..., httpx2.Response]:
    def fake(url: str, **kwargs: Any) -> httpx2.Response:
        calls.append({"url": url, **kwargs})
        return response

    return fake


def _stub(verb: str, response: httpx2.Response) -> tuple[Bound, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    return bind_stubs(**{verb: _recording(calls, response)}), calls


def _row(runner_id: str, name: str, **fields: Any) -> dict[str, Any]:
    return {
        "runner_id": runner_id,
        "runner_name": name,
        "connection": "online",
        "online": True,
        "added_at": "2026-10-06T00:00:00Z",
        "added_by": "ada",
        "workspace_id": "w1",
        "hub_paused": False,
        "locally_paused": False,
        **fields,
    }


_NEVER_CONNECTED = {"connection": "never_connected", "online": False, "workspace_id": None, "registered_at": None}


def test_add_posts_the_name_and_prints_the_id_and_the_token_once() -> None:
    bound, calls = _stub("post", _respond(201, _ADDED))

    result = bound.runner.invoke(hub_group, ["runner", "add", "r-claude"], env=ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"] == f"{HUB_URL}/api/runners"
    assert calls[0]["json"] == {"name": "r-claude"}
    assert f"added runner {_ADDED['runner_id']} (r-claude) — never connected" in result.output
    assert "BZ_HUB_TOKEN_R_CLAUDE=sekrit-token" in result.output.splitlines()
    assert result.output.count("sekrit-token") == 1


@pytest.mark.parametrize(
    ("name", "variable"),
    [("r-chatgpt", "BZ_HUB_TOKEN_R_CHATGPT"), ("Mixed.Case 9", "BZ_HUB_TOKEN_MIXED_CASE_9")],
)
def test_add_prints_the_variable_its_runners_name_derives(name: str, variable: str) -> None:
    bound, _ = _stub("post", _respond(201, {**_ADDED, "runner_name": name}))

    result = bound.runner.invoke(hub_group, ["runner", "add", name], env=ENV)

    assert f"{variable}=sekrit-token" in result.output.splitlines()


def test_enroll_prints_the_variable_of_the_name_the_registry_entry_holds() -> None:
    """The enrollment answers no name, so the line's variable comes from the registry entry the verb reads beside it."""
    runner_id = str(_ADDED["runner_id"])
    posted: list[dict[str, Any]] = []
    read: list[dict[str, Any]] = []
    bound = bind_stubs(
        post=_recording(posted, _respond(201, {"runner_id": runner_id, "token": "rotated"})),
        get=_recording(read, _respond(200, _row(runner_id, "r-chatgpt"))),
    )

    result = bound.runner.invoke(hub_group, ["runner", "enroll", runner_id], env=ENV)

    assert result.exit_code == 0, result.output
    assert posted[0]["url"] == f"{HUB_URL}/api/runners/{runner_id}/enrollments"
    assert read[0]["url"] == f"{HUB_URL}/api/runners/{runner_id}"
    assert "BZ_HUB_TOKEN_R_CHATGPT=rotated" in result.output.splitlines()
    assert "BZ_HUB_TOKEN=rotated" not in result.output.splitlines()


def test_enroll_rotates_nothing_when_the_registry_entry_cannot_be_read() -> None:
    """The name is read before the token is rotated, so a token is never minted that no line shows."""
    runner_id = str(_ADDED["runner_id"])
    posted: list[dict[str, Any]] = []
    bound = bind_stubs(
        post=_recording(posted, _respond(201, {"runner_id": runner_id, "token": "rotated"})),
        get=_recording([], _respond(503, {"detail": "unavailable"})),
    )

    result = bound.runner.invoke(hub_group, ["runner", "enroll", runner_id], env=ENV)

    assert result.exit_code != 0
    assert posted == []
    assert "rotated" not in result.output


def test_add_under_json_prints_the_raw_body() -> None:
    bound, _ = _stub("post", _respond(201, _ADDED))

    result = bound.runner.invoke(hub_group, ["runner", "add", "r-claude", "--json"], env=ENV)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _ADDED


@pytest.mark.parametrize(("status_code", "detail"), [(404, "Not Found"), (405, "Method Not Allowed")])
def test_add_against_a_hub_without_the_add_route_says_the_hub_predates_it(status_code: int, detail: str) -> None:
    bound, _ = _stub("post", _respond(status_code, {"detail": detail}))

    result = bound.runner.invoke(hub_group, ["runner", "add", "r-claude"], env=ENV)

    assert result.exit_code != 0
    assert f"this hub ({HUB_URL}) predates `hub runner add`" in result.output


def test_list_shows_each_runner_s_id_beside_its_name_and_a_never_connected_one_as_such() -> None:
    rows = [
        _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A", "twin"),
        _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B", "twin", **_NEVER_CONNECTED),
    ]
    bound, _ = _stub("get", _respond(200, {"runners": rows}))

    result = bound.runner.invoke(hub_group, ["runner", "list"], env=ENV)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A  twin             online          ws=w1",
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B  twin             never-connected ws=-",
    ]


def test_list_widens_the_name_column_to_the_longest_name_so_the_state_column_lines_up() -> None:
    rows = [
        _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A", "a-name-past-sixteen-chars"),
        _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B", "twin", **_NEVER_CONNECTED),
    ]
    bound, _ = _stub("get", _respond(200, {"runners": rows}))

    result = bound.runner.invoke(hub_group, ["runner", "list"], env=ENV)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A  a-name-past-sixteen-chars online          ws=w1",
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B  twin                      never-connected ws=-",
    ]


def test_show_of_a_never_connected_runner_renders_no_missing_field_as_none() -> None:
    bound, _ = _stub("get", _respond(200, _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B", "r-claude", **_NEVER_CONNECTED)))

    result = bound.runner.invoke(hub_group, ["runner", "get", "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B"], env=ENV)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:2] == [
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B  r-claude  never-connected  ws=-",
        "  added=2026-10-06T00:00:00Z by ada",
    ]
    assert "None" not in result.output
