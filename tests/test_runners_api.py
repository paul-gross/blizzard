"""The operator runner registry — ``POST /api/runners`` and the by-id reads and writes beside it.

``GET /api/runners/{runner_id}`` is symmetric with ``GET /api/runners``: both render
:func:`~blizzard.hub.api.runners.registry_view`, a runner added but never registered included.
Every verb takes the hub-minted id, 404s on an unknown one, and rejects a runner's bearer token.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from blizzard.auth_core import Role
from blizzard.hub.domain.runners.registration import RunnerRegistration
from tests.support import HubHarness, build_hub, emitted_events, seed_runner, seed_session, seed_user

pytestmark = pytest.mark.component


def _add(hub: HubHarness, name: str = "r-claude", **kwargs: Any) -> dict[str, Any]:
    resp = hub.client.post("/api/runners", json={"name": name}, **kwargs)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


#: What the registry view shows of a runner before its first registration.
_NEVER_CONNECTED = (
    "runner_name",
    "connection",
    "online",
    "added_by",
    "workspace_id",
    "registered_at",
    "last_seen_at",
    "capabilities",
)


def _register(hub, runner_id: str = "r1", workspace_id: str = "w1") -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": workspace_id})
    assert resp.status_code == 201, resp.text


def test_get_runner_returns_the_same_view_the_list_carries(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    resp = hub.client.get("/api/runners/r1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["runner_id"] == "r1"
    assert resp.json()["workspace_id"] == "w1"

    listed = hub.client.get("/api/runners").json()["runners"]
    assert resp.json() == next(r for r in listed if r["runner_id"] == "r1")


def test_get_runner_returns_no_capabilities_when_none_were_registered(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    resp = hub.client.get("/api/runners/r1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["capabilities"] == []

    listed = hub.client.get("/api/runners").json()["runners"]
    assert next(r for r in listed if r["runner_id"] == "r1")["capabilities"] == []


def test_get_runner_and_list_runners_both_carry_every_registered_capability(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post(
        "/api/fleet/runners",
        json={
            "runner_id": "r1",
            "workspace_id": "w1",
            "capabilities": [
                {"harness_id": "claude", "version": "1.2.3", "tiers": ["sonnet"], "default": True, "available": True},
                {"harness_id": "codex", "version": None, "tiers": [], "default": False, "available": False},
            ],
        },
    )
    assert resp.status_code == 201, resp.text

    resp = hub.client.get("/api/runners/r1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["capabilities"] == [
        {"harness_id": "claude", "version": "1.2.3", "tiers": ["sonnet"], "default": True, "available": True},
        {"harness_id": "codex", "version": None, "tiers": [], "default": False, "available": False},
    ]

    listed = hub.client.get("/api/runners").json()["runners"]
    assert next(r for r in listed if r["runner_id"] == "r1")["capabilities"] == resp.json()["capabilities"]


def test_get_runner_unknown_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/runners/does-not-exist")
    assert resp.status_code == 404


def test_get_runner_reflects_pause_state(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    assert hub.client.post("/api/runners/r1/pause", json={"by": "op"}).status_code == 200

    resp = hub.client.get("/api/runners/r1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["hub_paused"] is True


def test_runner_bearer_token_is_rejected_on_get_runner(tmp_path: Path) -> None:
    from tests.test_fleet_auth import _seed_enrolled

    token = _seed_enrolled(tmp_path, runner_id="r1", workspace_id="w1")
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/runners/r1", headers=_bearer(token))
    assert resp.status_code == 403


def test_a_runner_s_own_token_cannot_add_a_runner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "r1")

    resp = hub.client.post("/api/runners", json={"name": "self-made"}, headers=_bearer(token))

    assert resp.status_code == 403
    assert [r["runner_id"] for r in hub.client.get("/api/runners").json()["runners"]] == ["r1"]


def test_add_mints_an_id_and_token_and_the_runner_shows_never_connected(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    added = _add(hub, "  r-claude  ")

    assert added["runner_id"].startswith("rn_")
    assert added["runner_name"] == "r-claude"
    identity = hub.client.get("/api/fleet/identity", headers=_bearer(added["token"]))
    assert identity.json() == {"runner_id": added["runner_id"], "runner_name": "r-claude"}
    shown = hub.client.get(f"/api/runners/{added['runner_id']}")
    assert shown.status_code == 200, shown.text
    assert "token" not in shown.json()
    assert {key: shown.json()[key] for key in _NEVER_CONNECTED} == {
        "runner_name": "r-claude",
        "connection": "never_connected",
        "online": False,
        "added_by": "operator",
        "workspace_id": None,
        "registered_at": None,
        "last_seen_at": None,
        "capabilities": [],
    }
    assert hub.client.get("/api/runners").json()["runners"] == [shown.json()]


def test_an_added_runner_shows_online_from_its_first_registration(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add(hub)

    registered = hub.client.post(
        "/api/fleet/runners", json={"name": "r-claude", "workspace_id": "w1"}, headers=_bearer(added["token"])
    )

    assert registered.status_code == 201, registered.text
    shown = hub.client.get(f"/api/runners/{added['runner_id']}").json()
    assert (shown["connection"], shown["online"], shown["workspace_id"]) == ("online", True, "w1")
    assert shown["registered_at"] is not None


def test_add_publishes_a_runner_changed_frame_naming_who_added_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    added = _add(hub)

    frames = [json.loads(e["data"]) for e in emitted_events(hub) if e["event"] == "runner-changed"]
    assert frames == [
        {"runner_id": added["runner_id"], "runner_name": added["runner_name"], "kind": "added", "by": "operator"}
    ]


@pytest.mark.parametrize("name", ["", "   "])
def test_add_refuses_a_blank_name(tmp_path: Path, name: str) -> None:
    hub = build_hub(tmp_path)

    assert hub.client.post("/api/runners", json={"name": name}).status_code == 422
    assert hub.client.get("/api/runners").json()["runners"] == []


def test_two_runners_added_under_one_name_are_both_listed_oldest_first(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = _add(hub, "twin")
    hub.clock.advance(timedelta(seconds=1))
    second = _add(hub, "twin")

    listed = hub.client.get("/api/runners").json()["runners"]

    assert first["runner_id"] != second["runner_id"]
    assert [(r["runner_id"], r["runner_name"]) for r in listed] == [
        (first["runner_id"], "twin"),
        (second["runner_id"], "twin"),
    ]


def test_a_never_connected_runner_answers_every_by_id_verb(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    paused, revoked, retired = (_add(hub, name)["runner_id"] for name in ("paused", "revoked", "retired"))

    pause = hub.client.post(f"/api/runners/{paused}/pause", json={"by": "op"})
    revoke = hub.client.post(f"/api/runners/{revoked}/token-revocations", json={"by": "op"})
    retire = hub.client.post(f"/api/runners/{retired}/retire", json={"by": "op"})

    assert (pause.status_code, revoke.status_code, retire.status_code) == (200, 201, 200)
    assert (pause.json()["connection"], pause.json()["hub_paused"]) == ("never_connected", True)
    assert revoke.json()["runner"]["connection"] == "never_connected"
    assert (retire.json()["runner"]["connection"], retire.json()["runner"]["retired"]) == ("never_connected", True)


#: Each by-id operator route: its path after the id ("" is the GET), its body, and a route both twins take first.
_BY_ID_ROUTES = (
    pytest.param("", None, None, id="show"),
    pytest.param("/enrollments", None, None, id="enroll"),
    pytest.param("/pause", {"by": "op"}, None, id="pause"),
    pytest.param("/resume", {"by": "op"}, "/pause", id="resume"),
    pytest.param("/retire", {"by": "op"}, None, id="retire"),
    pytest.param("/reinstate", {"by": "op"}, "/retire", id="reinstate"),
    pytest.param("/token-revocations", {"by": "op"}, None, id="revoke-token"),
)


def _by_id(hub: HubHarness, runner_id: str, suffix: str, body: dict[str, str] | None) -> httpx.Response:
    path = f"/api/runners/{runner_id}{suffix}"
    return hub.client.post(path, json=body) if suffix else hub.client.get(path)


def _states(hub: HubHarness, runner_ids: list[str]) -> dict[str, RunnerRegistration | None]:
    return {runner_id: hub.services.registry.get_runner(runner_id) for runner_id in runner_ids}


@pytest.mark.parametrize(("suffix", "body", "first"), _BY_ID_ROUTES)
def test_a_by_id_route_misses_a_shared_name_and_reaches_only_the_runner_each_id_names(
    tmp_path: Path, suffix: str, body: dict[str, str] | None, first: str | None
) -> None:
    """A name two runners share resolves to neither — the route 404s and changes nothing — while
    each runner's id reaches that runner alone."""
    hub = build_hub(tmp_path)
    twins = [_add(hub, "twin")["runner_id"] for _ in range(2)]
    for runner_id in twins if first else ():
        assert _by_id(hub, runner_id, first or "", {"by": "op"}).is_success
    before = _states(hub, twins)

    missed = _by_id(hub, "twin", suffix, body)

    assert missed.status_code == 404, missed.text
    assert _states(hub, twins) == before
    for target, other in (twins, twins[::-1]):
        prior = _states(hub, twins)
        hit = _by_id(hub, target, suffix, body)
        assert hit.is_success, hit.text
        assert target in hit.text and other not in hit.text
        after = _states(hub, twins)
        assert after[other] == prior[other]
        assert (after[target] != prior[target]) == bool(suffix)


def test_adding_a_runner_and_rotating_its_token_are_refused_to_a_contributor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_session(hub, seed_user(hub, username="cora", role=Role.CONTRIBUTOR))
    admin = seed_session(hub, seed_user(hub, username="ada", role=Role.ADMIN))
    seed_runner(hub, "runner-a")

    for path, body in (("/api/runners", {"name": "r-claude"}), ("/api/runners/runner-a/enrollments", None)):
        refused = hub.client.post(path, json=body, headers=_cookie(contributor))
        assert refused.status_code == 403, f"{path} -> {refused.status_code}"
        assert "runner:add" in refused.json()["detail"]

    listed = hub.client.get("/api/runners", headers=_cookie(admin)).json()["runners"]
    assert [r["runner_id"] for r in listed] == ["runner-a"]
    added = _add(hub, headers=_cookie(admin))
    assert hub.client.get(f"/api/runners/{added['runner_id']}", headers=_cookie(admin)).json()["added_by"] == "ada"
