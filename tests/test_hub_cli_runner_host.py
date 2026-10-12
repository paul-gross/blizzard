"""``blizzard hub runner-host …`` and ``blizzard hub runner add --host`` — the rendering half with ``httpx`` stubbed
(unit tier), and the CLI driven against a real hub app (component tier). ``blizzard hub host`` stays the daemon
launcher."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group
from tests.support import HubHarness, build_hub, seed_runner

_HUB_URL = "http://hub.local:8421"
_ENV = {"BZ_HUB_URL": _HUB_URL}
_ADDED = {"host_id": "hs_01JC4Z8Q3XKQ1V5W2M7N9P0R6T", "host_name": "box", "token": "sekrit-token", "adopted": []}


def _respond(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", _HUB_URL))


def _stub(monkeypatch: pytest.MonkeyPatch, verb: str, response: httpx.Response) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(httpx, verb, fake)
    return calls


def _row(host_id: str, name: str, **fields: Any) -> dict[str, Any]:
    return {"host_id": host_id, "host_name": name, "connection": "never_connected", "online": False, **fields}


@pytest.mark.unit
def test_add_posts_the_name_and_adoptions_and_prints_the_id_and_the_token_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, "post", _respond(201, {**_ADDED, "adopted": ["rn_a", "rn_b"]}))

    result = CliRunner().invoke(
        hub_group, ["runner-host", "add", "box", "--adopt", "rn_a", "--adopt", "rn_b"], env=_ENV
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["url"] == f"{_HUB_URL}/api/hosts"
    assert calls[0]["json"] == {"name": "box", "adopt": ["rn_a", "rn_b"]}
    assert f"added host {_ADDED['host_id']} (box) — never connected" in result.output
    assert "adopted: rn_a, rn_b" in result.output
    assert "BZ_HUB_TOKEN=sekrit-token" in result.output.splitlines()
    assert result.output.count("sekrit-token") == 1


@pytest.mark.unit
def test_add_names_the_refusal_of_a_taken_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, "post", _respond(409, {"detail": "a live host is already named 'box'"}))

    result = CliRunner().invoke(hub_group, ["runner-host", "add", "box"], env=_ENV)

    assert result.exit_code != 0
    assert "already named 'box'" in result.output


@pytest.mark.unit
def test_list_shows_each_hosts_id_beside_its_name_and_a_never_connected_one_as_such(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"hosts": [_row("hs_a", "box", runner_ids=["rn_1", "rn_2"]), _row("hs_b", "other")]}
    calls = _stub(monkeypatch, "get", _respond(200, body))

    result = CliRunner().invoke(hub_group, ["runner-host", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"] == f"{_HUB_URL}/api/hosts"
    lines = result.output.splitlines()
    assert any(line.startswith("hs_a  box") and "never-connected" in line and "runners=2" in line for line in lines)
    assert any(line.startswith("hs_b  other") and "runners=0" in line for line in lines)


@pytest.mark.unit
def test_list_all_asks_for_retired_hosts_and_marks_them(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"hosts": [_row("hs_a", "box", retired=True, retired_at="2026-10-12T00:00:00Z", retired_by="op")]}
    calls = _stub(monkeypatch, "get", _respond(200, body))

    result = CliRunner().invoke(hub_group, ["runner-host", "list", "--all"], env=_ENV)

    assert calls[0]["params"] == {"include_retired": "true"}
    assert "[retired 2026-10-12T00:00:00Z by op]" in result.output


@pytest.mark.unit
def test_an_empty_registry_says_how_to_add_a_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, "get", _respond(200, {"hosts": []}))

    result = CliRunner().invoke(hub_group, ["runner-host", "list"], env=_ENV)

    assert "no hosts — add one with `blizzard hub runner-host add <name>`" in result.output


@pytest.mark.unit
def test_show_renders_a_never_connected_host_without_none(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _row("hs_a", "box", added_at="2026-10-12T00:00:00Z", enrolled=True, runner_ids=[])
    _stub(monkeypatch, "get", _respond(200, body))

    result = CliRunner().invoke(hub_group, ["runner-host", "show", "hs_a"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert "None" not in result.output


@pytest.mark.unit
def test_show_of_an_unknown_host_exits_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, "get", _respond(404, {"detail": "unknown host hs_x"}))

    result = CliRunner().invoke(hub_group, ["runner-host", "show", "hs_x"], env=_ENV)

    assert result.exit_code == 1
    assert "unknown host hs_x" in result.output


@pytest.mark.unit
@pytest.mark.parametrize(("verb", "path"), [("adopt", "adoptions"), ("release", "releases")])
def test_adopt_and_release_post_the_runner_ids(monkeypatch: pytest.MonkeyPatch, verb: str, path: str) -> None:
    calls = _stub(monkeypatch, "post", _respond(200, _row("hs_a", "box")))

    result = CliRunner().invoke(hub_group, ["runner-host", verb, "hs_a", "rn_1", "rn_2", "--by", "op"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"] == f"{_HUB_URL}/api/hosts/hs_a/{path}"
    assert calls[0]["json"] == {"by": "op", "runner_ids": ["rn_1", "rn_2"]}


@pytest.mark.unit
def test_adopt_needs_at_least_one_runner() -> None:
    assert CliRunner().invoke(hub_group, ["runner-host", "adopt", "hs_a"], env=_ENV).exit_code == 2


@pytest.mark.unit
def test_enroll_prints_the_new_token_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, "post", _respond(201, {"host_id": "hs_a", "token": "fresh"}))

    result = CliRunner().invoke(hub_group, ["runner-host", "enroll", "hs_a"], env=_ENV)

    assert calls[0]["url"] == f"{_HUB_URL}/api/hosts/hs_a/enrollments"
    assert "BZ_HUB_TOKEN=fresh" in result.output.splitlines()
    assert result.output.count("fresh") == 1


@pytest.mark.unit
def test_revoke_token_posts_to_the_revocations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, "post", _respond(201, {"host": _row("hs_a", "box")}))

    result = CliRunner().invoke(hub_group, ["runner-host", "revoke-token", "hs_a"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"] == f"{_HUB_URL}/api/hosts/hs_a/token-revocations"
    assert "revoked hs_a's token" in result.output


@pytest.mark.unit
def test_retire_posts_force_and_by_and_names_what_it_released(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, "post", _respond(200, {"host": _row("hs_a", "box"), "released_chunk_ids": ["ch_1"]}))

    result = CliRunner().invoke(hub_group, ["runner-host", "retire", "hs_a", "--force", "--by", "op"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"] == f"{_HUB_URL}/api/hosts/hs_a/retire"
    assert calls[0]["json"] == {"by": "op", "force": True}
    assert "released: ch_1" in result.output


@pytest.mark.unit
def test_retire_surfaces_the_holdings_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, "post", _respond(409, {"detail": "host hs_a has runners holding chunks — runner rn_a: ch_1"}))

    result = CliRunner().invoke(hub_group, ["runner-host", "retire", "hs_a"], env=_ENV)

    assert result.exit_code != 0
    assert "ch_1" in result.output


@pytest.mark.unit
def test_runner_add_host_sends_the_host_id_and_names_an_unknown_one(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, "post", _respond(404, {"detail": "unknown host hs_x"}))

    result = CliRunner().invoke(hub_group, ["runner", "add", "r", "--host", "hs_x"], env=_ENV)

    assert calls[0]["json"] == {"name": "r", "host_id": "hs_x"}
    assert result.exit_code != 0
    assert "unknown host hs_x" in result.output
    assert "predates" not in result.output


@pytest.mark.unit
def test_the_daemon_launcher_keeps_the_host_name() -> None:
    names = hub_group.list_commands(__import__("click").Context(hub_group))
    assert "host" in names and "runner-host" in names


def _against(monkeypatch: pytest.MonkeyPatch, hub: HubHarness) -> None:
    for verb in ("get", "post"):

        def call(url: str, *, _verb: str = verb, **kwargs: Any) -> httpx.Response:
            kwargs.pop("timeout", None)
            return getattr(hub.client, _verb)(url.removeprefix(_HUB_URL), **kwargs)

        monkeypatch.setattr(httpx, verb, call)


def _json_of(output: str) -> dict[str, Any]:
    """The verb's own ``--json`` body: the in-process hub logs JSON lines to the same captured stdout, each with an
    ``event`` key the body lacks."""
    for line in reversed(output.splitlines()):
        if line.startswith("{") and "event" not in json.loads(line):
            body: dict[str, Any] = json.loads(line)
            return body
    raise AssertionError(output)


@pytest.mark.component
def test_a_host_is_added_adopted_released_rotated_and_retired_through_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    _against(monkeypatch, hub)
    seed_runner(hub, "rn_a")

    added = CliRunner().invoke(hub_group, ["runner-host", "add", "box", "--json"], env=_ENV)
    assert added.exit_code == 0, added.output
    host_id = _json_of(added.output)["host_id"]
    assert host_id.startswith("hs_")

    listed = CliRunner().invoke(hub_group, ["runner-host", "list"], env=_ENV)
    assert any(line.startswith(host_id) and "never-connected" in line for line in listed.output.splitlines())

    runner = CliRunner().invoke(hub_group, ["runner", "add", "r2", "--host", host_id, "--json"], env=_ENV)
    assert runner.exit_code == 0, runner.output
    adopted = CliRunner().invoke(hub_group, ["runner-host", "adopt", host_id, "rn_a"], env=_ENV)
    again = CliRunner().invoke(hub_group, ["runner-host", "adopt", host_id, "rn_a"], env=_ENV)
    assert adopted.exit_code == 0, adopted.output
    assert again.exit_code != 0
    shown = CliRunner().invoke(hub_group, ["runner-host", "show", host_id], env=_ENV)
    assert "rn_a" in shown.output and _json_of(runner.output)["runner_id"] in shown.output

    released = CliRunner().invoke(hub_group, ["runner-host", "release", host_id, "rn_a"], env=_ENV)
    assert released.exit_code == 0, released.output

    revoked = CliRunner().invoke(hub_group, ["runner-host", "revoke-token", host_id], env=_ENV)
    assert revoked.exit_code == 0, revoked.output
    enrolled = CliRunner().invoke(hub_group, ["runner-host", "enroll", host_id], env=_ENV)
    assert enrolled.exit_code == 0 and "BZ_HUB_TOKEN=" in enrolled.output

    retired = CliRunner().invoke(hub_group, ["runner-host", "retire", host_id], env=_ENV)
    assert retired.exit_code == 0, retired.output
    assert CliRunner().invoke(hub_group, ["runner-host", "list"], env=_ENV).output.count(host_id) == 0
    assert CliRunner().invoke(hub_group, ["runner-host", "list", "--all"], env=_ENV).output.count(host_id) == 1


@pytest.mark.component
def test_runner_add_under_a_retired_host_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    _against(monkeypatch, hub)
    host_id = CliRunner().invoke(hub_group, ["runner-host", "add", "box", "--json"], env=_ENV)
    host = _json_of(host_id.output)["host_id"]
    CliRunner().invoke(hub_group, ["runner-host", "retire", host], env=_ENV)

    result = CliRunner().invoke(hub_group, ["runner", "add", "r", "--host", host], env=_ENV)

    assert result.exit_code != 0
    assert "retired" in result.output
