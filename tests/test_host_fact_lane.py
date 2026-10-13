"""The host's fact lane at the hub: ``POST /api/fleet/host/events`` lands a host's own subscription usage samples and
misses against a mark of the host's own (component tier)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.fact_kinds import EXTERNAL_SUBSCRIPTION_USAGE_MISSED, EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED
from tests.support import HubHarness, build_hub, count_queries, emitted_events, seed_runner
from tests.test_fleet_auth import _bearer

pytestmark = pytest.mark.component

_LANE = "/api/fleet/host/events"
_REPORT: dict[str, Any] = {
    "name": "box",
    "workspaces": [{"workspace_id": "w1", "env_capacity": 2}],
    "subscriptions": [{"slug": "anthropic", "name": "Anthropic", "provider": "anthropic"}],
}


def _sample(seq: int, slug: str = "anthropic", pct: float = 40.0) -> dict[str, Any]:
    return {
        "seq": seq,
        "kind": EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
        "payload": {
            "slug": slug,
            "name": slug.title(),
            "sampled_at": "2026-10-13T08:00:00+00:00",
            "windows": [
                {
                    "window": "5h",
                    "utilization_pct": pct,
                    "resets_at": "2026-10-13T12:00:00+00:00",
                    "window_seconds": 18000,
                }
            ],
        },
    }


def _miss(seq: int, slug: str = "anthropic") -> dict[str, Any]:
    return {
        "seq": seq,
        "kind": EXTERNAL_SUBSCRIPTION_USAGE_MISSED,
        "payload": {"slug": slug, "missed_at": "2026-10-13T08:30:00+00:00", "reason": "credential_lapsed"},
    }


def _host(hub: HubHarness, name: str = "box") -> dict[str, Any]:
    added = hub.client.post("/api/hosts", json={"name": name}).json()
    resp = hub.client.post("/api/fleet/host", json={**_REPORT, "name": name}, headers=_bearer(added["token"]))
    assert resp.status_code == 200, resp.text
    return added


def _push(hub: HubHarness, host: dict[str, Any], *facts: dict[str, Any]) -> dict[str, Any]:
    resp = hub.client.post(_LANE, json={"facts": list(facts)}, headers=_bearer(host["token"]))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_batch_is_applied_once_and_a_replay_is_reacknowledged(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)

    first = _push(hub, host, _sample(1), _miss(2))
    replay = _push(hub, host, _sample(1), _miss(2))

    assert first == {
        "host_id": host["host_id"],
        "high_water": 2,
        "applied": [1, 2],
        "already_applied": [],
        "rejected": [],
    }
    assert (replay["high_water"], replay["applied"], replay["already_applied"]) == (2, [], [1, 2])
    assert "route_ended" not in first


def test_a_retired_hosts_push_is_refused_and_its_mark_stays_put(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)
    _push(hub, host, _sample(1))
    assert hub.client.post(f"/api/hosts/{host['host_id']}/retire", json={}).status_code == 200

    resp = hub.client.post(_LANE, json={"facts": [_sample(2)]}, headers=_bearer(host["token"]))

    # Retirement revokes the host's token, so the gate no longer resolves it.
    assert resp.status_code == 401
    assert hub.services.hosts.high_water(host["host_id"]) == 1


def test_the_host_service_refuses_a_retired_host_whatever_token_admitted_it(tmp_path: Path) -> None:
    from blizzard.hub.domain.execution.facts import PushedFact
    from blizzard.hub.domain.runners.host import HostRetired

    hub = build_hub(tmp_path)
    host = _host(hub)
    held = hub.services.hosts.get_host(host["host_id"])
    assert held is not None
    assert hub.client.post(f"/api/hosts/{host['host_id']}/retire", json={}).status_code == 200
    retired = hub.services.hosts.get_host(host["host_id"])
    assert retired is not None and retired.retired

    with pytest.raises(HostRetired):
        hub.services.host_facts.ingest(
            retired, [PushedFact(1, EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED, _sample(1)["payload"])]
        )

    assert hub.services.hosts.high_water(host["host_id"]) == 0


def test_a_host_mark_and_a_runner_mark_advance_independently(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)
    runner_token = seed_runner(hub, "rn_a")

    _push(hub, host, _sample(1), _sample(2), _sample(3))
    runner = hub.client.post(
        "/api/fleet/events", json={"facts": [_sample(1), _sample(2)]}, headers=_bearer(runner_token)
    ).json()
    again = _push(hub, host, _sample(4))

    assert runner["applied"] == [1, 2]
    assert runner["high_water"] == 2
    assert again["applied"] == [4]
    assert again["high_water"] == 4


def test_an_unknown_kind_is_rejected_and_the_mark_stays_behind_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)

    ack = _push(hub, host, _sample(1), {"seq": 2, "kind": "lease.minted", "payload": {}}, _sample(3))

    assert ack["applied"] == [1, 3]
    assert ack["rejected"] == [2]
    assert ack["high_water"] == 3


def test_a_malformed_usage_fact_is_rejected(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)

    ack = _push(
        hub,
        host,
        {"seq": 1, "kind": EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED, "payload": {}},
        _miss(2) | {"payload": {"slug": "a"}},
    )

    assert ack["rejected"] == [1, 2]
    assert ack["high_water"] == 0


def test_a_runner_token_cannot_reach_the_lane(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "rn_a")

    assert hub.client.post(_LANE, json={"facts": []}, headers=_bearer(token)).status_code == 401
    assert hub.client.post(_LANE, json={"facts": []}).status_code == 401


def test_a_host_token_cannot_reach_the_runner_lane(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)

    assert hub.client.post("/api/fleet/events", json={"facts": []}, headers=_bearer(host["token"])).status_code == 401


# --- reading a host's usage ---------------------------------------------------


def _hosted_runner(hub: HubHarness, host: dict[str, Any], name: str, *, subscription: str | None) -> dict[str, Any]:
    added = hub.client.post("/api/runners", json={"name": name, "host_id": host["host_id"]})
    assert added.status_code == 201, added.text
    runner = added.json()
    registered = hub.client.post(
        "/api/fleet/runners",
        json={"workspace_id": "w1", "subscription": subscription},
        headers=_bearer(runner["token"]),
    )
    assert registered.status_code == 201, registered.text
    return runner


def _slugs(views: list[dict[str, Any]]) -> list[tuple[str, float | None]]:
    return [(v["slug"], v["windows"][0]["utilization_pct"] if v["windows"] else None) for v in views]


def test_a_hosted_runner_shows_its_hosts_newest_sample_for_the_slug_it_registered(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)
    runner = _hosted_runner(hub, host, "r1", subscription="anthropic")

    _push(hub, host, _sample(1, pct=10.0), _sample(2, pct=55.0))

    fleet_read = hub.client.get(f"/api/fleet/runners/{runner['runner_id']}", headers=_bearer(runner["token"])).json()
    operator_read = hub.client.get(f"/api/runners/{runner['runner_id']}").json()
    listed = {r["runner_id"]: r for r in hub.client.get("/api/runners").json()["runners"]}
    assert _slugs(fleet_read["subscriptions"]) == [("anthropic", 55.0)]
    assert _slugs(operator_read["subscriptions"]) == [("anthropic", 55.0)]
    assert _slugs(listed[runner["runner_id"]]["subscriptions"]) == [("anthropic", 55.0)]


def test_a_hosted_runner_with_no_slug_shows_none_and_a_sibling_slug_is_not_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)
    bare = _hosted_runner(hub, host, "bare", subscription=None)
    other = _hosted_runner(hub, host, "other", subscription="unreported")

    _push(hub, host, _sample(1))

    assert hub.client.get(f"/api/runners/{bare['runner_id']}").json()["subscriptions"] == []
    assert hub.client.get(f"/api/runners/{other['runner_id']}").json()["subscriptions"] == []


def test_the_host_lists_each_slug_once_naming_every_live_runner_spending_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)
    first = _hosted_runner(hub, host, "r1", subscription="anthropic")
    second = _hosted_runner(hub, host, "r2", subscription="anthropic")
    gone = _hosted_runner(hub, host, "r3", subscription="anthropic")
    _hosted_runner(hub, host, "r4", subscription=None)
    assert hub.client.post(f"/api/runners/{gone['runner_id']}/retire", json={}).status_code == 200

    _push(hub, host, _sample(1, pct=70.0), _miss(2))

    for read in (
        hub.client.get(f"/api/hosts/{host['host_id']}").json(),
        hub.client.get("/api/hosts").json()["hosts"][0],
    ):
        usage = read["subscription_usage"]
        assert [u["slug"] for u in usage] == ["anthropic"]
        assert sorted(usage[0]["runners"]) == sorted([first["runner_id"], second["runner_id"]])
        assert usage[0]["windows"][0]["utilization_pct"] == 70.0
        assert usage[0]["miss_reason"] == "credential_lapsed"
    runner_card = hub.client.get(f"/api/runners/{first['runner_id']}").json()["subscriptions"][0]
    host_card = hub.client.get(f"/api/hosts/{host['host_id']}").json()["subscription_usage"][0]
    assert {k: v for k, v in host_card.items() if k != "runners"} == runner_card


def test_a_runner_with_no_host_reports_and_shows_its_own_samples_as_before(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "rn_a")

    ack = hub.client.post("/api/fleet/events", json={"facts": [_sample(1, pct=33.0)]}, headers=_bearer(token)).json()

    assert ack["applied"] == [1]
    assert _slugs(hub.client.get("/api/runners/rn_a").json()["subscriptions"]) == [("anthropic", 33.0)]


def test_a_host_sample_publishes_one_external_usage_frame_per_spending_runner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)
    spender = _hosted_runner(hub, host, "r1", subscription="anthropic")
    _hosted_runner(hub, host, "r2", subscription="other")
    before = len(emitted_events(hub))

    _push(hub, host, _sample(1))
    _push(hub, host, _sample(1))

    frames = [e for e in emitted_events(hub)[before:] if e["event"] == "runner-changed"]
    assert [json.loads(f["data"]) for f in frames] == [
        {"runner_id": spender["runner_id"], "kind": "external-usage", "runner_name": "r1"}
    ]


def test_the_runner_list_statement_count_stays_flat_as_the_page_grows(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)
    _hosted_runner(hub, host, "r1", subscription="anthropic")
    _push(hub, host, _sample(1))
    one = count_queries(hub.engine, lambda: hub.services.registry.list_runners())
    for i in range(2, 6):
        _hosted_runner(hub, host, f"r{i}", subscription="anthropic")
    other = _host(hub, "other")
    _hosted_runner(hub, other, "o1", subscription="anthropic")

    many = count_queries(hub.engine, lambda: hub.services.registry.list_runners())

    assert many == one
