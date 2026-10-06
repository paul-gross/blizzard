"""``blizzard hub runner add|list|show`` — the rendering half with ``httpx`` stubbed (unit
tier), and the CLI driven against a real hub app (component tier), every by-id verb included."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner, Result

from blizzard.auth_core import Role
from blizzard.foundation.operator_sessions.internal.session_file import SessionFile
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.domain.runners.registration import RunnerRegistration
from tests.support import HubHarness, build_hub, seed_session, seed_user

_HUB_URL = "http://hub.local:8421"
_ENV = {"BZ_HUB_URL": _HUB_URL}
_ADDED = {"runner_id": "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6T", "runner_name": "r-claude", "token": "sekrit-token"}


def _respond(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", _HUB_URL))


def _stub(monkeypatch: pytest.MonkeyPatch, verb: str, response: httpx.Response) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(httpx, verb, fake)
    return calls


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


@pytest.mark.unit
def test_add_posts_the_name_and_prints_the_id_and_the_token_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, "post", _respond(201, _ADDED))

    result = CliRunner().invoke(hub_group, ["runner", "add", "r-claude"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"] == f"{_HUB_URL}/api/runners"
    assert calls[0]["json"] == {"name": "r-claude"}
    assert f"added runner {_ADDED['runner_id']} (r-claude) — never connected" in result.output
    assert "BZ_HUB_TOKEN=sekrit-token" in result.output.splitlines()
    assert result.output.count("sekrit-token") == 1


@pytest.mark.unit
def test_add_under_json_prints_the_raw_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, "post", _respond(201, _ADDED))

    result = CliRunner().invoke(hub_group, ["runner", "add", "r-claude", "--json"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _ADDED


@pytest.mark.unit
@pytest.mark.parametrize(("status_code", "detail"), [(404, "Not Found"), (405, "Method Not Allowed")])
def test_add_against_a_hub_without_the_add_route_says_the_hub_predates_it(
    monkeypatch: pytest.MonkeyPatch, status_code: int, detail: str
) -> None:
    _stub(monkeypatch, "post", _respond(status_code, {"detail": detail}))

    result = CliRunner().invoke(hub_group, ["runner", "add", "r-claude"], env=_ENV)

    assert result.exit_code != 0
    assert f"this hub ({_HUB_URL}) predates `hub runner add`" in result.output


@pytest.mark.unit
def test_list_shows_each_runner_s_id_beside_its_name_and_a_never_connected_one_as_such(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A", "twin"),
        _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B", "twin", **_NEVER_CONNECTED),
    ]
    _stub(monkeypatch, "get", _respond(200, {"runners": rows}))

    result = CliRunner().invoke(hub_group, ["runner", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A  twin             online          ws=w1",
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B  twin             never-connected ws=-",
    ]


@pytest.mark.unit
def test_list_widens_the_name_column_to_the_longest_name_so_the_state_column_lines_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A", "a-name-past-sixteen-chars"),
        _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B", "twin", **_NEVER_CONNECTED),
    ]
    _stub(monkeypatch, "get", _respond(200, {"runners": rows}))

    result = CliRunner().invoke(hub_group, ["runner", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A  a-name-past-sixteen-chars online          ws=w1",
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B  twin                      never-connected ws=-",
    ]


@pytest.mark.unit
def test_show_of_a_never_connected_runner_renders_no_missing_field_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, "get", _respond(200, _row("rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B", "r-claude", **_NEVER_CONNECTED)))

    result = CliRunner().invoke(hub_group, ["runner", "show", "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:2] == [
        "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6B  r-claude  never-connected  ws=-",
        "  added=2026-10-06T00:00:00Z by ada",
    ]
    assert "None" not in result.output


def _against(monkeypatch: pytest.MonkeyPatch, hub: HubHarness) -> None:
    """Route the CLI's module-level ``httpx`` calls into ``hub``'s app."""
    for verb in ("get", "post"):

        def call(url: str, *, _verb: str = verb, **kwargs: Any) -> httpx.Response:
            kwargs.pop("timeout", None)
            return getattr(hub.client, _verb)(url.removeprefix(_HUB_URL), **kwargs)

        monkeypatch.setattr(httpx, verb, call)


@pytest.mark.component
def test_runners_added_under_one_name_both_list_never_connected_under_their_own_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    _against(monkeypatch, hub)

    added = [CliRunner().invoke(hub_group, ["runner", "add", "twin"], env=_ENV) for _ in range(2)]
    listed = CliRunner().invoke(hub_group, ["runner", "list"], env=_ENV)

    assert [a.exit_code for a in added] == [0, 0], [a.output for a in added]
    # The in-process hub logs to the same captured stdout, so pick out the verbs' own lines.
    ids = [line.split()[2] for a in added for line in a.output.splitlines() if line.startswith("added runner ")]
    assert len(set(ids)) == 2
    assert listed.exit_code == 0, listed.output
    assert sorted(line for line in listed.output.splitlines() if line.startswith("rn_")) == sorted(
        f"{runner_id}  twin             never-connected ws=-" for runner_id in ids
    )


@pytest.mark.component
def test_add_is_refused_to_a_signed_in_contributor_and_adds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    SessionFile.of().save(_HUB_URL, seed_session(hub, seed_user(hub, username="cora", role=Role.CONTRIBUTOR)))
    _against(monkeypatch, hub)

    result = CliRunner().invoke(hub_group, ["runner", "add", "r-claude"], env=_ENV)

    assert result.exit_code != 0
    assert "missing permission" in result.output
    assert "runner:add" in result.output
    assert hub.services.registry.list_runners() == []


#: Each by-id runner verb, and a verb both twins take first so a hit has something to change.
_BY_ID_VERBS = (
    pytest.param("show", None, id="show"),
    pytest.param("enroll", None, id="enroll"),
    pytest.param("pause", None, id="pause"),
    pytest.param("resume", "pause", id="resume"),
    pytest.param("retire", None, id="retire"),
    pytest.param("reinstate", "retire", id="reinstate"),
    pytest.param("revoke-token", None, id="revoke-token"),
)


def _runner_verb(verb: str, runner_id: str) -> Result:
    return CliRunner().invoke(hub_group, ["runner", verb, runner_id], env=_ENV)


def _states(hub: HubHarness, runner_ids: list[str]) -> dict[str, RunnerRegistration | None]:
    return {runner_id: hub.services.registry.get_runner(runner_id) for runner_id in runner_ids}


@pytest.mark.component
@pytest.mark.parametrize(("verb", "first"), _BY_ID_VERBS)
def test_a_by_id_verb_given_a_shared_name_exits_unknown_and_given_an_id_acts_on_that_runner_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str, first: str | None
) -> None:
    """A name two runners share resolves to neither — the verb exits unknown and changes nothing —
    while each runner's id reaches that runner alone."""
    hub = build_hub(tmp_path)
    _against(monkeypatch, hub)
    twins = [str(hub.client.post("/api/runners", json={"name": "twin"}).json()["runner_id"]) for _ in range(2)]
    for runner_id in twins if first else ():
        assert _runner_verb(first or "", runner_id).exit_code == 0
    before = _states(hub, twins)

    missed = _runner_verb(verb, "twin")

    assert missed.exit_code == 1 and "unknown runner twin" in missed.output, missed.output
    assert _states(hub, twins) == before
    for target, other in (twins, twins[::-1]):
        prior = _states(hub, twins)
        hit = _runner_verb(verb, target)
        assert hit.exit_code == 0, hit.output
        assert target in hit.output and other not in hit.output
        after = _states(hub, twins)
        assert after[other] == prior[other]
        assert (after[target] != prior[target]) == (verb != "show")
