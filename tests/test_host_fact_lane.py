"""The host's fact lane at the hub: ``POST /api/fleet/host/events`` lands a host's own subscription usage samples and
misses against a mark of the host's own (component tier). Two overlapping pushes of one host's facts apply each seq once
and never lower its mark."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from blizzard.foundation.fact_kinds import EXTERNAL_SUBSCRIPTION_USAGE_MISSED, EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED
from blizzard.hub.domain.execution.facts import PushedFact
from blizzard.hub.domain.execution.host_facts import HostFactIngestResult
from blizzard.hub.domain.kernel.high_water import HighWaterConflict
from blizzard.hub.domain.runners.host import Host
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.transactions import HubTransactions
from tests.support import HubHarness, build_hub, count_queries, emitted_events, hub_store_connections, seed_runner
from tests.test_fleet_auth import bearer

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
    resp = hub.client.post("/api/fleet/host", json={**_REPORT, "name": name}, headers=bearer(added["token"]))
    assert resp.status_code == 200, resp.text
    return added


def _push(hub: HubHarness, host: dict[str, Any], *facts: dict[str, Any]) -> dict[str, Any]:
    resp = hub.client.post(_LANE, json={"facts": list(facts)}, headers=bearer(host["token"]))
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


# --- overlapping pushes of one host's facts -------------------------------------


def _held(hub: HubHarness, host: dict[str, Any]) -> Host:
    held = hub.services.hosts.get_host(host["host_id"])
    assert held is not None
    return held


def _samples(seqs: range, *, marker: float = 0.0) -> list[PushedFact]:
    """One sample per seq at ``seq * 10 + marker`` percent, so the stored row names the seq and push it landed for."""
    return [
        PushedFact(seq, EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED, _sample(seq, pct=seq * 10 + marker)["payload"])
        for seq in seqs
    ]


def _stored_pcts(hub: HubHarness, host_id: str) -> list[float]:
    usage = hub.services.hosts.usage_of([host_id])[host_id]
    return [window.utilization_pct for sample in usage.samples for window in sample.windows]


def _after_first_mark_read(monkeypatch: pytest.MonkeyPatch, competitor: Callable[[], None]) -> None:
    """Patch the host high-water read so its first call reads, then runs ``competitor`` to completion on a second
    thread before answering the mark it read; every later call, the competitor's own included, reads as it would."""
    original = HostRegistryStore.high_water
    armed = [True]

    def compete_after_read(self: HostRegistryStore, host_id: str) -> int:
        mark = original(self, host_id)
        if armed:
            armed.clear()
            raised: list[BaseException] = []

            def run() -> None:
                try:
                    competitor()
                except BaseException as exc:  # surfaced on the paused push's thread below
                    raised.append(exc)

            thread = Thread(target=run)
            thread.start()
            thread.join(timeout=30)
            assert not thread.is_alive(), "the competing push never finished"
            if raised:
                raise raised[0]
        return mark

    monkeypatch.setattr(HostRegistryStore, "high_water", compete_after_read)


@pytest.mark.parametrize(
    ("paused", "competing"),
    [(range(1, 2), range(1, 3)), (range(1, 3), range(1, 3))],
    ids=["shorter-paused-push", "same-seqs"],
)
def test_a_push_paused_after_its_mark_read_applies_no_seq_a_competing_push_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paused: range, competing: range
) -> None:
    """Push A reads the mark, then push B carrying the same seqs (or more) applies them all and advances the mark
    before A writes: A answers every seq already applied and wrote no slug, the stored sample is B's newest, and the
    mark stays at B's highest seq — A neither overwrites B's sample nor moves the mark back to its own."""
    hub = build_hub(tmp_path)
    held = _held(hub, _host(hub))
    service = hub.services.host_facts
    competed: list[HostFactIngestResult] = []

    _after_first_mark_read(monkeypatch, lambda: competed.append(service.ingest(held, _samples(competing))))
    pushed = service.ingest(held, _samples(paused, marker=5.0))

    assert competed[0].applied == list(competing)
    assert (pushed.applied, pushed.already_applied, pushed.rejected) == ([], list(paused), [])
    assert pushed.slugs == frozenset()
    assert _stored_pcts(hub, held.host_id) == [competing[-1] * 10]
    assert pushed.high_water == hub.services.hosts.high_water(held.host_id) == competing[-1]


def test_a_push_resumes_from_the_mark_a_competing_push_left_below_its_own_seqs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Push B lands only the first of A's seqs: A answers that one already applied and applies the rest against the
    mark B left."""
    hub = build_hub(tmp_path)
    held = _held(hub, _host(hub))
    service = hub.services.host_facts
    competed: list[HostFactIngestResult] = []

    _after_first_mark_read(monkeypatch, lambda: competed.append(service.ingest(held, _samples(range(1, 2)))))
    pushed = service.ingest(held, _samples(range(1, 4), marker=5.0))

    assert competed[0].applied == [1]
    assert (pushed.applied, pushed.already_applied) == ([2, 3], [1])
    assert _stored_pcts(hub, held.host_id) == [35.0]
    assert pushed.high_water == hub.services.hosts.high_water(held.host_id) == 3


def test_a_mid_batch_crash_lands_neither_the_in_flight_fact_nor_its_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each fact's upsert and its mark advance commit together: a crash after the second fact's upsert returns and
    before its mark advance rolls that upsert back with it, leaving the first fact's sample and mark, so a lost-ack
    replay answers the first already applied and applies the second once."""
    hub = build_hub(tmp_path)
    held = _held(hub, _host(hub))
    original = HostRegistryStore.record_external_usage
    calls = [0]

    def crash_on_second_call(self: HostRegistryStore, host_id: str, **fields: Any) -> None:
        calls[0] += 1
        original(self, host_id, **fields)
        if calls[0] == 2:
            # Seq 2's sample is written; its mark advance is not.
            raise RuntimeError("simulated crash mid-batch")

    monkeypatch.setattr(HostRegistryStore, "record_external_usage", crash_on_second_call)
    with pytest.raises(RuntimeError, match="simulated crash mid-batch"):
        hub.services.host_facts.ingest(held, _samples(range(1, 4)))
    monkeypatch.undo()

    assert _stored_pcts(hub, held.host_id) == [10.0]
    assert hub.services.hosts.high_water(held.host_id) == 1
    replay = hub.services.host_facts.ingest(held, _samples(range(1, 3)))
    assert (replay.applied, replay.already_applied) == ([2], [1])
    assert _stored_pcts(hub, held.host_id) == [20.0]


def _stored_miss_slugs(hub: HubHarness, host_id: str) -> list[str]:
    store = hub_store_connections(hub.engine)
    missed = s.host_external_usage_misses
    with store.read("stored_miss_slugs") as conn:
        return sorted(conn.execute(store.select(missed, missed.c.slug).where(missed.c.host_id == host_id)).scalars())


def test_a_mid_batch_crash_rolls_the_in_flight_miss_back_with_its_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A miss joins its fact's unit as a sample does: a crash after the second miss's upsert returns and before its
    mark advance leaves no row for that seq's slug, and the mark at the first miss's seq."""
    hub = build_hub(tmp_path)
    held = _held(hub, _host(hub))
    original = HostRegistryStore.record_external_usage_miss
    calls = [0]

    def crash_on_second_call(self: HostRegistryStore, host_id: str, **fields: Any) -> None:
        calls[0] += 1
        original(self, host_id, **fields)
        if calls[0] == 2:
            # Seq 2's miss is written; its mark advance is not.
            raise RuntimeError("simulated crash mid-batch")

    monkeypatch.setattr(HostRegistryStore, "record_external_usage_miss", crash_on_second_call)
    misses = [
        PushedFact(seq, EXTERNAL_SUBSCRIPTION_USAGE_MISSED, _miss(seq, slug)["payload"])
        for seq, slug in ((1, "first"), (2, "second"))
    ]
    with pytest.raises(RuntimeError, match="simulated crash mid-batch"):
        hub.services.host_facts.ingest(held, misses)

    assert _stored_miss_slugs(hub, held.host_id) == ["first"]
    assert hub.services.hosts.high_water(held.host_id) == 1


def test_a_conflict_on_a_mark_no_push_moved_surfaces_rather_than_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflict whose re-read finds the mark where the push read it names no competing push: the push raises it
    instead of deciding again forever, and the fact's upsert rolls back with its unit."""
    hub = build_hub(tmp_path)
    held = _held(hub, _host(hub))
    attempts: list[int] = []

    def refuse(self: HostRegistryStore, host_id: str, *, seq: int, expected: int, **_: Any) -> None:
        attempts.append(seq)
        if len(attempts) > 3:
            raise AssertionError("the push kept deciding again on a mark that never moved")
        raise HighWaterConflict(host_id, expected=expected)

    monkeypatch.setattr(HostRegistryStore, "set_high_water", refuse)
    with pytest.raises(HighWaterConflict):
        hub.services.host_facts.ingest(held, _samples(range(1, 2)))

    assert attempts == [1]
    assert _stored_pcts(hub, held.host_id) == []


def test_the_host_mark_advances_only_from_the_seq_its_push_last_read(tmp_path: Path) -> None:
    """An advance decided on a mark since moved — a stale seq, or a first row another push inserted — is refused with
    :class:`HighWaterConflict` and moves nothing; one from the standing seq lands."""
    hub = build_hub(tmp_path)
    store = hub_store_connections(hub.engine)
    hosts = HostRegistryStore(store)
    transactions = HubTransactions(store)
    at = hub.clock.now()

    def advance(seq: int, *, expected: int) -> None:
        with transactions.begin() as unit:
            hosts.set_high_water("hs_a", seq=seq, expected=expected, at=at, transaction=unit)

    advance(13, expected=0)
    with pytest.raises(HighWaterConflict):
        advance(12, expected=9)
    with pytest.raises(HighWaterConflict):
        advance(12, expected=0)
    assert hosts.high_water("hs_a") == 13

    advance(14, expected=13)
    assert hosts.high_water("hs_a") == 14


def test_a_first_host_mark_colliding_with_no_row_in_scope_is_a_store_fault_not_a_conflict(tmp_path: Path) -> None:
    """A host id another tenant's mark row already holds collides on the first insert, yet no push of this tenant's
    moved the mark: the store raises the fault, never a conflict a caller would decide again on."""
    hub = build_hub(tmp_path)
    store = hub_store_connections(hub.engine)
    at = hub.clock.now()
    with hub.engine.begin() as conn:
        conn.execute(s.host_high_water.insert().values(host_id="hs_a", seq=5, updated_at=at, tenant_id="ten_other"))

    with pytest.raises(HubStoreError), HubTransactions(store).begin() as unit:
        HostRegistryStore(store).set_high_water("hs_a", seq=1, expected=0, at=at, transaction=unit)


def test_a_retired_hosts_push_is_refused_and_its_mark_stays_put(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)
    _push(hub, host, _sample(1))
    assert hub.client.post(f"/api/hosts/{host['host_id']}/retire", json={}).status_code == 200

    resp = hub.client.post(_LANE, json={"facts": [_sample(2)]}, headers=bearer(host["token"]))

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
        "/api/fleet/events", json={"facts": [_sample(1), _sample(2)]}, headers=bearer(runner_token)
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

    assert hub.client.post(_LANE, json={"facts": []}, headers=bearer(token)).status_code == 401
    assert hub.client.post(_LANE, json={"facts": []}).status_code == 401


def test_a_host_token_cannot_reach_the_runner_lane(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = _host(hub)

    assert hub.client.post("/api/fleet/events", json={"facts": []}, headers=bearer(host["token"])).status_code == 401


# --- reading a host's usage ---------------------------------------------------


def _hosted_runner(hub: HubHarness, host: dict[str, Any], name: str, *, subscription: str | None) -> dict[str, Any]:
    added = hub.client.post("/api/runners", json={"name": name, "host_id": host["host_id"]})
    assert added.status_code == 201, added.text
    runner = added.json()
    registered = hub.client.post(
        "/api/fleet/runners",
        json={"workspace_id": "w1", "subscription": subscription},
        headers=bearer(runner["token"]),
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

    fleet_read = hub.client.get(f"/api/fleet/runners/{runner['runner_id']}", headers=bearer(runner["token"])).json()
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

    ack = hub.client.post("/api/fleet/events", json={"facts": [_sample(1, pct=33.0)]}, headers=bearer(token)).json()

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
