"""``blizzard hub cadence`` and its ``chore`` verbs (component tier) — a real ``build_hosted_app`` the CLI's requests
are routed into, so each verb meets the hub's own validation, revision check, and attention, and the flag notice
reads the hub's own flags answer."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
import pytest
from click.testing import Result
from fastapi.testclient import TestClient

from blizzard.foundation.flags import resolve_flags
from blizzard.hub.cli import hub as hub_group
from tests.support.config_inputs import FLAG_REGISTRY, build_hosted_hub_app, init_hub
from tests.support.worker_http import Bound, bind_stubs

_HUB = "http://hub.local:8421"
_GRAPH = """
name: alpha
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""
_NOTICE = "cadences mint nothing until the flag cadences is on"

_bound: dict[str, Bound] = {}


def _serve(tmp_path: Path, flags: dict[str, bool]) -> Iterator[TestClient]:
    config = dataclasses.replace(
        init_hub(tmp_path / "hub"), flags=flags, flag_resolution=resolve_flags(FLAG_REGISTRY, "hub", flags)
    )
    with TestClient(build_hosted_hub_app(config)) as c:

        def forward(method: str, url: str, *, timeout: float, **kwargs: Any) -> httpx2.Response:
            return c.request(method.upper(), urlsplit(url).path, **kwargs)

        _bound["hub"] = bind_stubs(request=forward)
        assert c.post("/api/graphs", json={"definition_yaml": _GRAPH}).status_code == 201
        created = c.post(
            "/api/routines", json={"slug": "arch", "graph_name": "alpha", "default_scope_slug": "blizzard"}
        )
        assert created.status_code == 201, created.text
        yield c


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    yield from _serve(tmp_path, {})


@pytest.fixture
def flag_on_client(tmp_path: Path) -> Iterator[TestClient]:
    yield from _serve(tmp_path, {"cadences": True})


def _cli(*args: str) -> Result:
    return _bound["hub"].runner.invoke(hub_group, ["cadence", *args], env={"BZ_HUB_URL": _HUB})


def _ok(*args: str) -> Result:
    result = _cli(*args)
    assert result.exit_code == 0, result.output
    return result


def _shown(slug: str = "evening") -> dict[str, Any]:
    return json.loads(_ok("get", slug, "--json").stdout)


def test_create_list_and_show_with_the_flag_notice_on_stderr_while_off(client: TestClient) -> None:
    _ok("create", "evening", "--cron", "0 20 * * *", "--zone", "America/Chicago", "--spacing", "10d")

    shown = _cli("get", "evening", "--json")
    assert shown.exit_code == 0, shown.output
    body = json.loads(shown.stdout)
    assert (body["slug"], body["spacing_minutes"], body["anchor"]) == ("evening", 14400, "started")
    assert body["attention"] == [{"reason": "no_chores", "chore": None, "scope": None}]
    assert _NOTICE in shown.stderr
    assert _NOTICE not in shown.stdout

    listed = _cli("list")
    assert listed.exit_code == 0, listed.output
    assert "slug=evening" in listed.stdout
    assert _NOTICE in listed.stderr


def test_the_flag_notice_is_absent_while_the_flag_is_on(flag_on_client: TestClient) -> None:
    _ok("create", "evening", "--spacing", "1d")

    for args in (("get", "evening"), ("list",)):
        result = _ok(*args)
        assert _NOTICE not in result.stderr


def test_a_bad_create_is_refused_naming_the_field(client: TestClient) -> None:
    result = _cli("create", "evening", "--cron", "0 0 * * * *", "--zone", "UTC")
    assert result.exit_code != 0
    assert "schedule.cron" in result.output


def test_chore_add_edit_and_remove_keep_the_id_and_move_the_revision(client: TestClient) -> None:
    _ok("create", "evening", "--spacing", "10d")

    _ok("chore", "add", "evening", "arch", "--routine", "arch", "--all-scopes", "--mode", "delta")
    added = _shown()
    [chore] = added["chores"]
    assert (chore["key"], chore["scopes"], chore["mode"], chore["promote"]) == ("arch", "all", "delta", True)
    assert chore["chore_id"].startswith("chr_")
    assert added["attention"] == []

    _ok("chore", "edit", "evening", "arch", "--hold")
    held = _shown()
    assert held["chores"][0]["chore_id"] == chore["chore_id"]
    assert held["chores"][0]["promote"] is False
    assert held["revision"] == added["revision"] + 1

    _ok("chore", "edit", "evening", "arch", "--promote", "--default-scope")
    promoted = _shown()["chores"][0]
    assert (promoted["promote"], promoted["scopes"], promoted["chore_id"]) == (True, None, chore["chore_id"])

    _ok("chore", "edit", "evening", "arch", "--scope", "blizzard")
    assert _shown()["chores"][0]["scopes"] == ["blizzard"]

    _ok("chore", "add", "evening", "ghost", "--routine", "nope", "--hold")
    assert {"reason": "routine_unknown", "chore": "ghost", "scope": None} in _shown()["attention"]

    _ok("chore", "remove", "evening", "ghost")
    assert [c["key"] for c in _shown()["chores"]] == ["arch"]


def test_chore_verbs_refuse_a_duplicate_or_missing_key(client: TestClient) -> None:
    _ok("create", "evening", "--spacing", "10d")
    _ok("chore", "add", "evening", "arch", "--routine", "arch")

    assert "already has chore 'arch'" in _cli("chore", "add", "evening", "arch", "--routine", "arch").output
    assert "has no chore 'nope'" in _cli("chore", "edit", "evening", "nope", "--hold").output
    assert "has no chore 'nope'" in _cli("chore", "remove", "evening", "nope").output
    both = _cli("chore", "edit", "evening", "arch", "--all-scopes", "--default-scope")
    assert both.exit_code != 0


def test_edit_clears_the_schedule_and_the_spacing_with_its_anchor(client: TestClient) -> None:
    _ok("create", "evening", "--cron", "0 20 * * *", "--zone", "UTC", "--spacing", "2h", "--anchor", "finished")

    _ok("edit", "evening", "--no-spacing")
    cleared = _shown()
    assert (cleared["spacing_minutes"], cleared["anchor"]) == (None, None)
    assert cleared["schedule"] == {"cron": "0 20 * * *", "zone": "UTC"}

    _ok("edit", "evening", "--zone", "America/Chicago")
    assert _shown()["schedule"] == {"cron": "0 20 * * *", "zone": "America/Chicago"}

    _ok("edit", "evening", "--no-schedule", "--spacing", "3d")
    edited = _shown()
    assert (edited["schedule"], edited["spacing_minutes"]) == (None, 3 * 24 * 60)


def test_retire_hides_it_from_the_list_and_enable_restores_it(client: TestClient) -> None:
    _ok("create", "evening", "--spacing", "10d")

    assert "is now retired" in _ok("retire", "evening").output
    assert "slug=evening" not in _ok("list").stdout
    assert "slug=evening" in _ok("list", "--include-retired").stdout
    assert "is now enabled" in _ok("enable", "evening").output
    assert "slug=evening" in _ok("list").stdout


def test_an_unknown_cadence_is_refused(client: TestClient) -> None:
    result = _cli("get", "nope")
    assert result.exit_code != 0
    assert "unknown cadence 'nope'" in result.output


def test_errands_lists_each_errand_with_its_state_and_get_prints_the_counts(client: TestClient) -> None:
    _ok("create", "evening", "--spacing", "10d")
    _ok("chore", "add", "evening", "arch", "--routine", "arch")
    _ok("chore", "add", "evening", "ghost", "--routine", "nope")

    listed = _ok("errands", "evening")
    rows = json.loads(_ok("errands", "evening", "--json").stdout)

    assert [(r["chore"], r["scope"], r["state"], r["last_run"]) for r in rows] == [("arch", "blizzard", "due", None)]
    assert rows[0]["due_at"]
    assert "arch  scope=blizzard  due  due_at=" in listed.stdout
    assert "last run: none" in listed.stdout
    assert "errands: due=1  running=0  waiting=0  stopped=0" in _ok("get", "evening").stdout
    assert _shown()["errand_counts"] == {"due": 1, "running": 0, "waiting": 0, "stopped": 0}


def test_errands_of_an_unknown_cadence_is_refused(client: TestClient) -> None:
    result = _cli("errands", "nope")
    assert result.exit_code != 0
    assert "unknown cadence 'nope'" in result.output
