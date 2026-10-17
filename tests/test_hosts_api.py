"""The host record, its credential, and the host verbs at the hub (component tier).

A host's bearer token reaches exactly the five ``/api/fleet/host*`` routes; a runner's token reaches none of them, and
every other ``/api/fleet`` route refuses a host's. The operator verbs live under ``/api/hosts``."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.domain.runners.registration import STALE_AFTER
from tests.support import HubHarness, TenantHarness, build_hub, emitted_events, pointer_token, seed_runner

pytestmark = pytest.mark.component

_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: done
"""

_REPORT: dict[str, Any] = {
    "name": "box",
    "url": "http://box.local:9000",
    "redirect_uris": ["http://box.local:9000/auth/callback"],
    "workspaces": [{"workspace_id": "w1", "env_capacity": 2}],
    "subscriptions": [{"slug": "claude-max", "name": "Claude Max", "provider": "anthropic"}],
}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_host(hub: HubHarness | TenantHarness, name: str = "box", **body: Any) -> dict[str, Any]:
    resp = hub.client.post("/api/hosts", json={"name": name, **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _host_frames(hub: HubHarness, since: int = 0) -> list[dict[str, str]]:
    return [e for e in emitted_events(hub, since=since) if e["event"] == "host-changed"]


def _last_event_id(hub: HubHarness) -> int:
    events = emitted_events(hub)
    return int(events[-1]["id"]) if events else 0


def _held_chunk(hub: HubHarness, runner_id: str) -> str:
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "664"})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    assert claim.status_code == 201, claim.text
    return str(chunk_id)


def test_add_prints_a_prefixed_id_and_a_token_once_and_lists_the_host_never_connected(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    added = _add_host(hub)

    assert added["host_id"].startswith("hs_")
    assert added["token"]
    listed = hub.client.get("/api/hosts").json()["hosts"]
    assert [h["host_id"] for h in listed] == [added["host_id"]]
    assert listed[0]["connection"] == "never_connected"
    assert listed[0]["registered_at"] is None
    assert "token" not in listed[0]
    assert hub.client.get(f"/api/hosts/{added['host_id']}").json() == listed[0]
    frames = _host_frames(hub)
    assert len(frames) == 1
    assert json.loads(frames[0]["data"]) == {
        "host_id": added["host_id"],
        "host_name": "box",
        "kind": "added",
        "by": "operator",
    }


def test_a_blank_name_is_refused_and_a_live_name_is_taken(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _add_host(hub)

    assert hub.client.post("/api/hosts", json={"name": "  "}).status_code == 422
    taken = hub.client.post("/api/hosts", json={"name": "box"})
    assert taken.status_code == 409
    assert "box" in taken.text
    assert len(hub.client.get("/api/hosts").json()["hosts"]) == 1


def test_a_retired_hosts_name_can_be_held_again(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = _add_host(hub)
    assert hub.client.post(f"/api/hosts/{first['host_id']}/retire", json={}).status_code == 200

    second = _add_host(hub)

    assert second["host_id"] != first["host_id"]


def test_unknown_host_is_a_404_on_every_by_id_verb(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert hub.client.get("/api/hosts/hs_nope").status_code == 404
    runners = {"runner_ids": ["rn_x"]}
    assert hub.client.post("/api/hosts/hs_nope/adoptions", json=runners).status_code == 404
    assert hub.client.post("/api/hosts/hs_nope/releases", json=runners).status_code == 404
    assert hub.client.post("/api/hosts/hs_nope/enrollments").status_code == 404
    assert hub.client.post("/api/hosts/hs_nope/token-revocations", json={}).status_code == 404
    assert hub.client.post("/api/hosts/hs_nope/retire", json={}).status_code == 404


def test_two_identical_reports_publish_one_frame_and_the_first_stamps_registered_at(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    since = _last_event_id(hub)

    first = hub.client.post("/api/fleet/host", json=_REPORT, headers=_bearer(added["token"]))
    second = hub.client.post("/api/fleet/host", json=_REPORT, headers=_bearer(added["token"]))

    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["registered_at"] is not None
    assert first.json()["host_id"] == added["host_id"]
    assert first.json()["tenant_id"]
    assert first.json()["host_name"] == "box"
    frames = _host_frames(hub, since)
    assert len(frames) == 1
    assert json.loads(frames[0]["data"])["kind"] == "reported"


def test_a_changed_report_or_one_after_going_offline_publishes_again(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    headers = _bearer(added["token"])
    hub.client.post("/api/fleet/host", json=_REPORT, headers=headers)
    since = _last_event_id(hub)

    hub.client.post("/api/fleet/host", json={**_REPORT, "url": "http://other:1"}, headers=headers)
    assert len(_host_frames(hub, since)) == 1

    hub.client.post("/api/fleet/host", json={**_REPORT, "url": "http://other:1"}, headers=headers)
    assert len(_host_frames(hub, since)) == 1

    hub.clock.advance(STALE_AFTER + timedelta(seconds=1))
    hub.client.post("/api/fleet/host", json={**_REPORT, "url": "http://other:1"}, headers=headers)
    assert len(_host_frames(hub, since)) == 2

    hub.client.post("/api/fleet/host", json={**_REPORT, "url": "http://other:1", "workspaces": []}, headers=headers)
    assert len(_host_frames(hub, since)) == 3


def test_the_report_replaces_what_the_hub_holds_whole(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    headers = _bearer(added["token"])

    hub.client.post("/api/fleet/host", json=_REPORT, headers=headers)
    hub.client.post("/api/fleet/host", json={"name": "box", "url": "http://x", "workspaces": []}, headers=headers)

    view = hub.client.get(f"/api/hosts/{added['host_id']}").json()
    assert view["url"] == "http://x"
    assert view["workspaces"] == []
    assert view["subscriptions"] == []
    assert view["redirect_uris"] == []
    assert view["connection"] == "online"


def test_a_reported_name_another_live_host_holds_is_not_recorded(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _add_host(hub, "alpha")
    beta = _add_host(hub, "beta")

    resp = hub.client.post("/api/fleet/host", json={**_REPORT, "name": "alpha"}, headers=_bearer(beta["token"]))

    assert resp.status_code == 200, resp.text
    assert resp.json()["host_name"] == "beta"


def test_a_blank_reported_name_keeps_the_held_one_and_a_free_one_renames(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub, "alpha")
    headers = _bearer(added["token"])

    assert (
        hub.client.post("/api/fleet/host", json={**_REPORT, "name": " "}, headers=headers).json()["host_name"]
        == "alpha"
    )
    assert (
        hub.client.post("/api/fleet/host", json={**_REPORT, "name": "gamma"}, headers=headers).json()["host_name"]
        == "gamma"
    )


def test_a_host_adds_a_runner_under_itself_and_reads_its_runners(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    headers = _bearer(added["token"])
    since = _last_event_id(hub)

    created = hub.client.post("/api/fleet/host/runners", json={"name": "r-one"}, headers=headers)

    assert created.status_code == 201, created.text
    runner_id = created.json()["runner_id"]
    assert runner_id.startswith("rn_")
    assert created.json()["token"]
    shown = hub.client.get(f"/api/runners/{runner_id}").json()
    assert shown["host_id"] == added["host_id"]
    record = hub.client.get("/api/fleet/host", headers=headers).json()
    assert record["runners"] == [{"runner_id": runner_id, "runner_name": "r-one"}]
    assert any(e["event"] == "runner-changed" and "added" in e["data"] for e in emitted_events(hub, since=since))
    assert hub.client.get(f"/api/hosts/{added['host_id']}").json()["runner_ids"] == [runner_id]


def test_the_runner_a_host_adds_registers_with_its_own_token(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    created = hub.client.post("/api/fleet/host/runners", json={"name": "r-one"}, headers=_bearer(added["token"])).json()

    resp = hub.client.post("/api/fleet/runners", json={"workspace_id": "w1"}, headers=_bearer(created["token"]))

    assert resp.status_code == 201, resp.text


def test_a_host_token_reaches_no_other_fleet_route_and_a_runner_token_reaches_no_host_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    runner_token = seed_runner(hub, "r1")
    host = _bearer(added["token"])
    runner = _bearer(runner_token)

    assert hub.client.post("/api/fleet/runners", json={"workspace_id": "w1"}, headers=host).status_code == 401
    assert hub.client.get("/api/fleet/runners/r1", headers=host).status_code == 401
    assert hub.client.get("/api/fleet/queue/peek", headers=host).status_code == 401
    assert (
        hub.client.post("/api/fleet/routes", json={"chunk_id": "ch_x", "runner_id": "r1"}, headers=host).status_code
        == 401
    )
    assert hub.client.get("/api/fleet/host", headers=runner).status_code == 401
    assert hub.client.post("/api/fleet/host", json=_REPORT, headers=runner).status_code == 401
    assert hub.client.post("/api/fleet/host/runners", json={"name": "x"}, headers=runner).status_code == 401
    assert hub.client.post("/api/fleet/host/runners/r1/retire", json={}, headers=runner).status_code == 401
    assert hub.client.get("/api/fleet/host").status_code == 401


def test_a_host_token_is_no_operator_credential(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)

    resp = hub.client.post("/api/runners", json={"name": "r"}, headers=_bearer(added["token"]))

    assert resp.status_code == 403


def test_adopt_places_a_standalone_runner_and_refuses_it_a_second_time(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    seed_runner(hub, "rn_a")
    seed_runner(hub, "rn_b")
    url = f"/api/hosts/{added['host_id']}/adoptions"

    ok = hub.client.post(url, json={"runner_ids": ["rn_a"]})
    again = hub.client.post(url, json={"runner_ids": ["rn_a"]})
    other = _add_host(hub, "other")
    elsewhere = hub.client.post(f"/api/hosts/{other['host_id']}/adoptions", json={"runner_ids": ["rn_a"]})

    assert ok.status_code == 200, ok.text
    assert ok.json()["runner_ids"] == ["rn_a"]
    assert again.status_code == 409
    assert elsewhere.status_code == 409
    assert hub.client.get("/api/runners/rn_a").json()["host_id"] == added["host_id"]
    assert hub.client.get("/api/runners/rn_b").json()["host_id"] is None


def test_adopt_is_all_or_none_and_refuses_a_retired_or_unknown_runner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    seed_runner(hub, "rn_a")
    seed_runner(hub, "rn_dead")
    assert hub.client.post("/api/runners/rn_dead/retire", json={"by": "op"}).status_code == 200
    url = f"/api/hosts/{added['host_id']}/adoptions"

    assert hub.client.post(url, json={"runner_ids": ["rn_a", "rn_dead"]}).status_code == 409
    assert hub.client.post(url, json={"runner_ids": ["rn_a", "rn_nobody"]}).status_code == 404
    assert hub.client.get(f"/api/hosts/{added['host_id']}").json()["runner_ids"] == []


def test_another_tenants_runner_is_unknown_to_adopt_and_to_add_adopt(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    mine = hub.add_tenant("mine")
    other = hub.add_tenant("other")
    added = _add_host(mine)
    seed_runner(other.services, other.runner_id("r1"), register=False)

    adopt = mine.client.post(f"/api/hosts/{added['host_id']}/adoptions", json={"runner_ids": [other.runner_id("r1")]})
    add = mine.client.post("/api/hosts", json={"name": "second", "adopt": [other.runner_id("r1")]})

    assert adopt.status_code == add.status_code == 404
    assert "another tenant" not in adopt.text + add.text
    assert [h["host_name"] for h in mine.client.get("/api/hosts").json()["hosts"]] == ["box"]
    assert other.client.get(f"/api/runners/{other.runner_id('r1')}").json()["host_id"] is None


def test_add_with_adopt_places_runners_in_one_write_and_a_refusal_adds_no_host(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, "rn_a")
    seed_runner(hub, "rn_b")

    refused = hub.client.post("/api/hosts", json={"name": "bad", "adopt": ["rn_a", "rn_missing"]})
    assert refused.status_code == 404
    assert hub.client.get("/api/hosts").json()["hosts"] == []

    added = _add_host(hub, "good", adopt=["rn_a", "rn_b"])
    assert added["adopted"] == ["rn_a", "rn_b"]
    assert hub.client.get(f"/api/hosts/{added['host_id']}").json()["runner_ids"] == ["rn_a", "rn_b"]

    again = hub.client.post("/api/hosts", json={"name": "worse", "adopt": ["rn_a"]})
    assert again.status_code == 409
    assert [h["host_name"] for h in hub.client.get("/api/hosts").json()["hosts"]] == ["good"]


def test_release_keeps_the_runners_id_and_token(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    token = seed_runner(hub, "rn_a")
    hub.client.post(f"/api/hosts/{added['host_id']}/adoptions", json={"runner_ids": ["rn_a"]})

    released = hub.client.post(f"/api/hosts/{added['host_id']}/releases", json={"runner_ids": ["rn_a"]})

    assert released.status_code == 200, released.text
    assert released.json()["runner_ids"] == []
    assert hub.client.get("/api/runners/rn_a").json()["host_id"] is None
    registered = hub.client.post("/api/fleet/runners", json={"workspace_id": "w1"}, headers=_bearer(token))
    assert registered.status_code == 201
    assert registered.json()["runner_id"] == "rn_a"
    host_view = hub.client.get("/api/fleet/host", headers=_bearer(added["token"])).json()
    assert host_view["runners"] == []


def test_release_refuses_a_runner_that_does_not_stand_under_the_host(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    seed_runner(hub, "rn_a")

    resp = hub.client.post(f"/api/hosts/{added['host_id']}/releases", json={"runner_ids": ["rn_a"]})

    assert resp.status_code == 404


def test_revoke_token_refuses_the_old_token_by_name_and_enroll_issues_a_new_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    host_id = added["host_id"]
    assert hub.client.get("/api/fleet/host", headers=_bearer(added["token"])).status_code == 200

    revoked = hub.client.post(f"/api/hosts/{host_id}/token-revocations", json={})
    again = hub.client.post(f"/api/hosts/{host_id}/token-revocations", json={})
    refused = hub.client.get("/api/fleet/host", headers=_bearer(added["token"]))
    enrolled = hub.client.post(f"/api/hosts/{host_id}/enrollments")

    assert revoked.status_code == 201, revoked.text
    assert revoked.json()["host"]["enrolled"] is False
    assert again.status_code == 409
    assert refused.status_code == 401
    assert "revoked" in refused.text
    assert enrolled.status_code == 201
    assert enrolled.json()["token"] != added["token"]
    assert hub.client.get("/api/fleet/host", headers=_bearer(enrolled.json()["token"])).status_code == 200
    assert hub.client.get("/api/fleet/host", headers=_bearer(added["token"])).status_code == 401


def test_enroll_rotates_a_held_token(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)

    new = hub.client.post(f"/api/hosts/{added['host_id']}/enrollments").json()["token"]

    assert hub.client.get("/api/fleet/host", headers=_bearer(added["token"])).status_code == 401
    assert hub.client.get("/api/fleet/host", headers=_bearer(new)).status_code == 200


def test_retire_revokes_the_credential_and_a_retired_hosts_token_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    host_id = added["host_id"]
    headers = _bearer(added["token"])
    hub.client.post("/api/fleet/host", json=_REPORT, headers=headers)
    since = _last_event_id(hub)

    retired = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "op"})
    rerun = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "op"})

    assert retired.status_code == 200, retired.text
    assert retired.json()["host"]["retired"] is True
    assert retired.json()["host"]["retired_by"] == "op"
    assert rerun.status_code == 200
    assert hub.client.get("/api/fleet/host", headers=headers).status_code == 401
    assert hub.client.post(f"/api/hosts/{host_id}/enrollments").status_code == 409
    assert hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": ["rn_a"]}).status_code in {404, 409}
    assert [h["host_id"] for h in hub.client.get("/api/hosts").json()["hosts"]] == []
    assert len(hub.client.get("/api/hosts", params={"include_retired": "true"}).json()["hosts"]) == 1
    assert len([f for f in _host_frames(hub, since) if "retired" in f["data"]]) == 1


def test_a_token_enrolled_on_a_retired_host_is_refused_403(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    host_id = added["host_id"]
    hub.client.post(f"/api/hosts/{host_id}/retire", json={})
    hosts = hub.services.hosts
    from blizzard.foundation.tokens import TokenHash
    from blizzard.hub.domain.runners.host import HostTokenRotation

    hosts.rotate_token(  # type: ignore[attr-defined]
        HostTokenRotation(host_id, TokenHash("late").hex, hub.clock.now(), "racer")
    )

    resp = hub.client.get("/api/fleet/host", headers=_bearer("late"))

    assert resp.status_code == 403


def test_retire_refuses_while_a_runner_under_the_host_holds_a_chunk_unless_forced(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    host_id = added["host_id"]
    seed_runner(hub, "rn_a")
    seed_runner(hub, "rn_b")
    hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": ["rn_a", "rn_b"]})
    chunk_id = _held_chunk(hub, "rn_a")

    refused = hub.client.post(f"/api/hosts/{host_id}/retire", json={})

    assert refused.status_code == 409
    assert chunk_id in refused.text
    assert "rn_a" in refused.text
    assert hub.client.get(f"/api/hosts/{host_id}").json()["retired"] is False
    assert hub.client.get("/api/runners/rn_a").json()["retired"] is False
    assert hub.client.get("/api/fleet/host", headers=_bearer(added["token"])).status_code == 200

    forced = hub.client.post(f"/api/hosts/{host_id}/retire", json={"force": True})

    assert forced.status_code == 200, forced.text
    assert forced.json()["released_chunk_ids"] == [chunk_id]
    assert hub.client.get(f"/api/hosts/{host_id}").json()["retired"] is True
    assert hub.client.get("/api/runners/rn_a").json()["retired"] is True
    assert hub.client.get("/api/runners/rn_b").json()["retired"] is True
    assert hub.client.get("/api/fleet/host", headers=_bearer(added["token"])).status_code == 401


def test_a_rerun_over_a_retired_host_retires_the_runners_a_crash_left_unretired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    host_id = added["host_id"]
    seed_runner(hub, "rn_a")
    hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": ["rn_a"]})
    host = hub.services.hosts.get_host(host_id)
    assert host is not None
    fact = host.retire({}, force=True, by="op", at=hub.clock.now())
    hub.services.hosts.record_retirement(host_id, fact=fact, at=hub.clock.now(), by="op")  # type: ignore[attr-defined]
    assert hub.client.get("/api/runners/rn_a").json()["retired"] is False

    rerun = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "op"})

    assert rerun.status_code == 200, rerun.text
    assert hub.client.get("/api/runners/rn_a").json()["retired"] is True


def test_a_runner_gaining_a_route_after_the_check_is_a_409_and_the_committed_host_retirement_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    host_id = added["host_id"]
    seed_runner(hub, "rn_a")
    hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": ["rn_a"]})
    chunk_id = _held_chunk(hub, "rn_a")
    fleet = hub.services.fleet
    real = fleet.holdings
    reads: list[str] = []

    def stale_first(runner_id: str):  # type: ignore[no-untyped-def]
        reads.append(runner_id)
        return [] if len(reads) == 1 else real(runner_id)

    monkeypatch.setattr(fleet, "holdings", stale_first)
    since = _last_event_id(hub)

    refused = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "op"})

    assert refused.status_code == 409, refused.text
    assert chunk_id in refused.json()["detail"]
    assert "rn_a" in refused.json()["detail"]
    assert hub.client.get(f"/api/hosts/{host_id}").json()["retired"] is True
    assert hub.client.get("/api/runners/rn_a").json()["retired"] is False
    assert len([f for f in _host_frames(hub, since) if "retired" in f["data"]]) == 1


def test_a_rerun_over_a_crash_left_runner_holding_a_chunk_is_a_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    host_id = added["host_id"]
    seed_runner(hub, "rn_a")
    hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": ["rn_a"]})
    chunk_id = _held_chunk(hub, "rn_a")
    host = hub.services.hosts.get_host(host_id)
    assert host is not None
    fact = host.retire({}, force=True, by="op", at=hub.clock.now())
    hub.services.hosts.record_retirement(host_id, fact=fact, at=hub.clock.now(), by="op")  # type: ignore[attr-defined]

    rerun = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "op"})

    assert rerun.status_code == 409, rerun.text
    assert chunk_id in rerun.json()["detail"]
    assert hub.client.get("/api/runners/rn_a").json()["retired"] is False


def test_a_host_retires_a_runner_under_itself_and_no_other(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    other = _add_host(hub, "other")
    headers = _bearer(added["token"])
    mine = hub.client.post("/api/fleet/host/runners", json={"name": "mine"}, headers=headers).json()["runner_id"]
    theirs = hub.client.post(
        "/api/fleet/host/runners", json={"name": "theirs"}, headers=_bearer(other["token"])
    ).json()["runner_id"]
    seed_runner(hub, "rn_alone")

    assert hub.client.post(f"/api/fleet/host/runners/{theirs}/retire", json={}, headers=headers).status_code == 404
    assert hub.client.post("/api/fleet/host/runners/rn_alone/retire", json={}, headers=headers).status_code == 404
    assert hub.client.post("/api/fleet/host/runners/rn_nobody/retire", json={}, headers=headers).status_code == 404
    retired = hub.client.post(f"/api/fleet/host/runners/{mine}/retire", json={"by": "box"}, headers=headers)

    assert retired.status_code == 200, retired.text
    assert retired.json()["runner"]["retired"] is True
    assert hub.client.get("/api/fleet/host", headers=headers).json()["runners"] == []


def test_a_host_refuses_to_retire_a_runner_holding_a_chunk_unless_forced(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)
    headers = _bearer(added["token"])
    seed_runner(hub, "rn_a")
    hub.client.post(f"/api/hosts/{added['host_id']}/adoptions", json={"runner_ids": ["rn_a"]})
    chunk_id = _held_chunk(hub, "rn_a")

    refused = hub.client.post("/api/fleet/host/runners/rn_a/retire", json={}, headers=headers)
    forced = hub.client.post("/api/fleet/host/runners/rn_a/retire", json={"force": True}, headers=headers)

    assert refused.status_code == 409
    assert chunk_id in refused.text
    assert forced.status_code == 200
    assert forced.json()["released_chunk_ids"] == [chunk_id]


def test_a_runner_added_under_an_unknown_or_retired_host_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = _add_host(hub)

    assert hub.client.post("/api/runners", json={"name": "r", "host_id": "hs_nope"}).status_code == 404
    ok = hub.client.post("/api/runners", json={"name": "r", "host_id": added["host_id"]})
    assert ok.status_code == 201, ok.text
    assert hub.client.get(f"/api/runners/{ok.json()['runner_id']}").json()["host_id"] == added["host_id"]
    hub.client.post(f"/api/hosts/{added['host_id']}/retire", json={"force": True})
    assert hub.client.post("/api/runners", json={"name": "r2", "host_id": added["host_id"]}).status_code == 409


def test_hosts_are_tenant_scoped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    mine = hub.add_tenant("mine")
    other = hub.add_tenant("other")
    added = _add_host(mine)

    assert other.client.get("/api/hosts").json()["hosts"] == []
    assert other.client.get(f"/api/hosts/{added['host_id']}").status_code == 404
    assert other.client.post(f"/api/hosts/{added['host_id']}/retire", json={}).status_code == 404
    assert hub.app is not None
    record = TestClient(hub.app).get("/api/fleet/host", headers=_bearer(added["token"])).json()
    assert record["tenant_id"] == mine.tenant
    assert other.client.post("/api/hosts", json={"name": "box"}).status_code == 201


def test_a_host_token_resolves_in_its_own_tenant_and_a_header_naming_another_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    mine = hub.add_tenant("mine")
    other = hub.add_tenant("other")
    added = _add_host(mine)

    resp = mine.client.get("/api/fleet/host", headers={**_bearer(added["token"]), "X-Blizzard-Tenant": other.tenant})

    assert resp.status_code == 400
