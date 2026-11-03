"""Usage facts land at the hub, idempotent, stale-epoch attributed (component tier).

Usage rides the same store-and-forward rails as ``lease.minted`` but is deliberately
NOT epoch-fenced: a stale-epoch row is real spend by a fenced-out zombie attempt and
must land, attributed to its own epoch. Proves idempotent ingest, totals, and SSE, and
that two overlapping pushes of one runner's facts apply each seq once and never lower
its high-water mark.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from blizzard.hub.domain.execution.facts import FactIngestResult, PushedFact
from blizzard.hub.domain.kernel.high_water import HighWaterConflict
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.transactions import HubTransactions
from tests.support import (
    HubHarness,
    build_hub,
    emitted_events,
    hub_store_connections,
    make_ready,
    pointer_token,
    report_lease,
)

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}


def _claim(hub: HubHarness) -> tuple[str, str]:
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    return chunk_id, node_id


def _usage_payload(
    node_id: str, *, epoch: int, cost_usd: float | None, estimated_cost_usd: float | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "chunk_id": "",  # filled by the caller
        "node_id": node_id,
        "epoch": epoch,
        "kind": "spawn",
        "model": "claude-opus-4-8",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "cache_create_tokens": 5,
        "cost_usd": cost_usd,
    }
    if estimated_cost_usd is not None:
        payload["estimated_cost_usd"] = estimated_cost_usd
    return payload


def _push_usage(
    hub: HubHarness,
    *,
    chunk_id: str,
    node_id: str,
    epoch: int,
    seq: int,
    cost_usd: float | None = 0.1,
    estimated_cost_usd: float | None = None,
) -> dict[str, object]:
    payload = _usage_payload(node_id, epoch=epoch, cost_usd=cost_usd, estimated_cost_usd=estimated_cost_usd)
    payload["chunk_id"] = chunk_id
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": seq, "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _usage_facts(chunk_id: str, node_id: str, seqs: range) -> list[PushedFact]:
    """One ``usage.recorded`` fact per seq, each costing ``seq`` cents, so a row names the seq it landed for."""
    facts = []
    for seq in seqs:
        payload = _usage_payload(node_id, epoch=1, cost_usd=seq / 100)
        payload["chunk_id"] = chunk_id
        facts.append(PushedFact(seq, "usage.recorded", payload))
    return facts


def _stored_costs(hub: HubHarness, chunk_id: str) -> list[float]:
    return sorted(step["cost_usd"] for step in hub.client.get(f"/api/chunks/{chunk_id}").json()["usage"])


def _seq_facts(kind: str, chunk_id: str, node_id: str, seqs: range) -> list[PushedFact]:
    """One fact of ``kind`` per seq, each naming its seq in the row it writes."""
    if kind == "usage.recorded":
        return _usage_facts(chunk_id, node_id, seqs)
    if kind == "event.recorded":
        payload = {"kind": "worker-lost", "severity": "critical", "chunk_id": chunk_id}
        return [PushedFact(seq, kind, {**payload, "message": f"seq {seq}"}) for seq in seqs]
    payload = {"chunk_id": chunk_id, "epoch": 1, "takeover_command": "claude --resume s"}
    return [PushedFact(seq, kind, {**payload, "detail": f"seq {seq}"}) for seq in seqs]


def _stored_seqs(hub: HubHarness, kind: str, chunk_id: str) -> list[int]:
    """The seq each row a fact of ``kind`` wrote names, one entry per row."""
    if kind == "usage.recorded":
        return [round(cost * 100) for cost in _stored_costs(hub, chunk_id)]
    store = hub_store_connections(hub.engine)
    column = s.event_log.c.message if kind == "event.recorded" else s.escalations.c.detail
    with store.read("stored_seqs") as conn:
        texts = conn.execute(store.select(column.table, column).where(column.table.c.chunk_id == chunk_id)).scalars()
        return sorted(int(text.removeprefix("seq ")) for text in texts if text.startswith("seq "))


def _after_first_mark_read(monkeypatch: pytest.MonkeyPatch, competitor: Callable[[], None]) -> None:
    """Patch the runner high-water read so its first call reads, then runs ``competitor`` to completion on a second
    thread before answering the mark it read; every later call, the competitor's own included, reads as it would."""
    original = ChunkRouteStore.runner_high_water
    armed = [True]

    def compete_after_read(self: ChunkRouteStore, runner_id: str) -> int:
        mark = original(self, runner_id)
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

    monkeypatch.setattr(ChunkRouteStore, "runner_high_water", compete_after_read)


@pytest.mark.parametrize("kind", ["usage.recorded", "event.recorded", "escalation.recorded"])
@pytest.mark.parametrize(
    ("paused", "competing"),
    [(range(2, 5), range(2, 5)), (range(2, 4), range(2, 5))],
    ids=["same-seqs", "shorter-paused-push"],
)
def test_a_push_paused_after_its_mark_read_applies_no_seq_a_competing_push_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, paused: range, competing: range
) -> None:
    """Push A reads the mark, then push B carrying the same seqs (or more) applies them all and advances the mark
    before A writes: A answers every seq already applied, each seq holds one row, an event publishes one frame per
    row, and the mark stays at B's highest seq — A neither duplicates B's rows nor moves the mark back to its own.
    An escalation's unit locks its chunk; the other kinds' units lock nothing."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    facts = hub.services.facts
    competed: list[FactIngestResult] = []
    since = hub.events.latest_id()

    _after_first_mark_read(
        monkeypatch, lambda: competed.append(facts.ingest("r1", _seq_facts(kind, chunk_id, node_id, competing)))
    )
    pushed = facts.ingest("r1", _seq_facts(kind, chunk_id, node_id, paused))

    assert competed[0].applied == list(competing)
    assert (pushed.applied, pushed.already_applied, pushed.rejected) == ([], list(paused), [])
    assert _stored_seqs(hub, kind, chunk_id) == list(competing)
    assert pushed.high_water == facts.ingest("r1", []).high_water == competing[-1]
    if kind == "event.recorded":
        logged = [event for event in emitted_events(hub, since=since) if event["event"] == "event-logged"]
        assert len(logged) == len(competing)


def test_a_push_resumes_from_the_mark_a_competing_push_left_below_its_own_seqs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Push B lands only the first of A's seqs: A answers that one already applied and applies the rest against the
    mark B left, each seq landing once."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    facts = hub.services.facts
    competed: list[FactIngestResult] = []

    _after_first_mark_read(
        monkeypatch, lambda: competed.append(facts.ingest("r1", _usage_facts(chunk_id, node_id, range(2, 3))))
    )
    pushed = facts.ingest("r1", _usage_facts(chunk_id, node_id, range(2, 5)))

    assert competed[0].applied == [2]
    assert (pushed.applied, pushed.already_applied) == ([3, 4], [2])
    assert _stored_costs(hub, chunk_id) == [pytest.approx(seq / 100) for seq in range(2, 5)]
    assert pushed.high_water == facts.ingest("r1", []).high_water == 4


def test_the_mark_advances_only_from_the_seq_its_push_last_read(tmp_path: Path) -> None:
    """An advance decided on a mark since moved — a stale seq, or a first row another push inserted — is refused with
    :class:`HighWaterConflict` and moves nothing; one from the standing seq lands."""
    hub = build_hub(tmp_path)
    store = hub_store_connections(hub.engine)
    route = ChunkRouteStore(store, hub.clock)
    transactions = HubTransactions(store)
    at = hub.clock.now()

    def advance(seq: int, *, expected: int) -> None:
        with transactions.begin() as unit:
            route.set_runner_high_water("r1", seq=seq, expected=expected, at=at, transaction=unit)

    advance(13, expected=0)
    with pytest.raises(HighWaterConflict):
        advance(12, expected=9)
    with pytest.raises(HighWaterConflict):
        advance(12, expected=0)
    assert route.runner_high_water("r1") == 13

    advance(14, expected=13)
    assert route.runner_high_water("r1") == 14


def test_a_first_mark_colliding_with_no_row_in_scope_is_a_store_fault_not_a_conflict(tmp_path: Path) -> None:
    """A runner id another tenant's mark row already holds collides on the first insert, yet no push of this
    tenant's moved the mark: the store raises the fault, never a conflict a caller would decide again on."""
    hub = build_hub(tmp_path)
    store = hub_store_connections(hub.engine)
    at = hub.clock.now()
    with hub.engine.begin() as conn:
        conn.execute(s.runner_high_water.insert().values(runner_id="r1", seq=5, updated_at=at, tenant_id="ten_other"))

    with pytest.raises(HubStoreError), HubTransactions(store).begin() as unit:
        ChunkRouteStore(store, hub.clock).set_runner_high_water("r1", seq=1, expected=0, at=at, transaction=unit)


def test_a_conflict_on_a_mark_no_push_moved_surfaces_rather_than_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflict whose re-read finds the mark where the push read it names no competing push: the push raises it
    instead of deciding again forever, and the fact's row rolls back with its unit."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    attempts: list[int] = []

    def refuse(self: ChunkRouteStore, runner_id: str, *, seq: int, expected: int, **_: Any) -> None:
        attempts.append(seq)
        if len(attempts) > 3:
            raise AssertionError("the push kept deciding again on a mark that never moved")
        raise HighWaterConflict(runner_id, expected=expected)

    monkeypatch.setattr(ChunkRouteStore, "set_runner_high_water", refuse)
    with pytest.raises(HighWaterConflict):
        hub.services.facts.ingest("r1", _usage_facts(chunk_id, node_id, range(2, 3)))

    assert attempts == [2]
    assert _stored_costs(hub, chunk_id) == []


def test_usage_fact_lands_per_step_and_derives_the_chunk_total(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    ack = _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)
    assert ack["applied"] == [2]

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert len(detail["usage"]) == 1
    step = detail["usage"][0]
    assert step["node_id"] == node_id
    assert step["epoch"] == 1
    assert step["kind"] == "spawn"
    assert step["model"] == "claude-opus-4-8"
    assert step["input_tokens"] == 100
    assert step["output_tokens"] == 50
    assert step["cache_read_tokens"] == 10
    assert step["cache_create_tokens"] == 5
    assert step["cost_usd"] == pytest.approx(0.10)

    assert detail["cost"]["input_tokens"] == 100
    assert detail["cost"]["cost_usd"] == pytest.approx(0.10)
    assert detail["cost"]["cost_partial"] is False

    listing = hub.client.get("/api/chunks").json()["chunks"]
    row = next(c for c in listing if c["chunk_id"] == chunk_id)
    assert row["cost"]["cost_usd"] == pytest.approx(0.10)
    assert row["cost"]["cost_partial"] is False


def test_usage_ingest_is_idempotent_by_seq_high_water(tmp_path: Path) -> None:
    """A replayed buffered usage fact lands once — the exact idempotency guarantee
    ``lease.minted`` already relies on (``tests/test_store_and_forward.py``)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    first = _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)
    assert first["applied"] == [2]

    replay = _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)
    assert replay["applied"] == [] and replay["already_applied"] == [2]

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert len(detail["usage"]) == 1  # not doubled
    assert detail["cost"]["cost_usd"] == pytest.approx(0.10)


def test_a_mid_batch_crash_persists_the_high_water_mark_at_the_last_applied_seq(tmp_path: Path) -> None:
    """Each fact's write and its mark advance commit together, not once after the whole batch: a crash
    partway through a multi-fact push leaves the mark at the last fact whose write landed, and the
    fact in flight lands neither its row nor its mark, so a lost-ack replay re-applies nothing."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    real_record_usage = hub.services.facts.usage.record_usage
    calls = {"n": 0}

    def _crash_on_second_call(chunk_id: str, **fields: Any) -> None:
        calls["n"] += 1
        real_record_usage(chunk_id, **fields)
        if calls["n"] == 2:
            # Seq 3's row is written; its mark advance is not.
            raise RuntimeError("simulated crash mid-batch")

    hub.services.facts.usage.record_usage = _crash_on_second_call
    try:
        payloads = [_usage_payload(node_id, epoch=1, cost_usd=cost) for cost in (0.10, 0.20, 0.30)]
        for payload in payloads:
            payload["chunk_id"] = chunk_id
        with pytest.raises(RuntimeError):
            hub.client.post(
                "/api/fleet/events",
                json={
                    "runner_id": "r1",
                    "facts": [
                        {"seq": 2, "kind": "usage.recorded", "payload": payloads[0]},
                        {"seq": 3, "kind": "usage.recorded", "payload": payloads[1]},
                        {"seq": 4, "kind": "usage.recorded", "payload": payloads[2]},
                    ],
                },
            )
    finally:
        hub.services.facts.usage.record_usage = real_record_usage

    # Seq 2's write and mark committed together before the crash; seq 3's unit rolled back its
    # written row with it, and seq 4 was never attempted — so seq 2 replays as already_applied
    # and seq 3 applies fresh, once.
    assert _stored_costs(hub, chunk_id) == [pytest.approx(0.10)]
    replay = _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)
    assert replay["applied"] == [] and replay["already_applied"] == [2]

    resume = _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=3, cost_usd=0.20)
    assert resume["applied"] == [3]

    # One row per seq: seq 2's and seq 3's, never a second seq 3 — and seq 4 never landed.
    assert _stored_costs(hub, chunk_id) == [pytest.approx(0.10), pytest.approx(0.20)]


def test_stale_epoch_usage_is_recorded_and_attributed_not_dropped(tmp_path: Path) -> None:
    """A usage row minted at an epoch behind the chunk's latest is real spend — it must
    land and be attributed to its own epoch, never dropped (unlike a stale completion,
    which the epoch fence rejects outright)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    report_lease(hub, chunk_id, epoch=2, seq=2)  # the chunk's latest epoch is now 2

    # A usage fact still carrying epoch=1 — a fenced-out zombie's already-incurred spend.
    ack = _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=3, cost_usd=0.20)
    assert ack["applied"] == [3]  # not rejected

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert len(detail["usage"]) == 1
    assert detail["usage"][0]["epoch"] == 1  # attributed to its own (stale) epoch
    assert detail["cost"]["cost_usd"] == pytest.approx(0.20)  # counted, not dropped


def test_cost_absent_usage_row_sums_tokens_and_flags_the_total_partial(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=None)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["usage"][0]["cost_usd"] is None
    assert detail["usage"][0]["cost_partial"] is True
    assert detail["cost"]["input_tokens"] == 100  # tokens still summed
    assert detail["cost"]["cost_usd"] == 0.0  # nothing to sum — the lower bound
    assert detail["cost"]["cost_partial"] is True


def test_estimate_only_row_is_shown_and_does_not_flag_the_total_partial(tmp_path: Path) -> None:
    """A row with no billed cost but a reported estimate is not a lower bound — it must
    not read PARTIAL, distinct from a row with neither amount."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=None, estimated_cost_usd=0.03)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["usage"][0]["cost_usd"] is None
    assert detail["usage"][0]["estimated_cost_usd"] == pytest.approx(0.03)
    assert detail["usage"][0]["cost_partial"] is False
    assert detail["cost"]["cost_usd"] == 0.0
    assert detail["cost"]["estimated_cost_usd"] == pytest.approx(0.03)
    assert detail["cost"]["cost_partial"] is False

    listing = hub.client.get("/api/chunks").json()["chunks"]
    row = next(c for c in listing if c["chunk_id"] == chunk_id)
    assert row["cost"]["estimated_cost_usd"] == pytest.approx(0.03)
    assert row["cost"]["cost_partial"] is False
    # No billed cost was recorded: the billed figure alone is a lower bound.
    assert detail["cost"]["billed_partial"] is True
    assert row["cost"]["billed_partial"] is True


def test_neither_amount_row_flags_the_total_partial(tmp_path: Path) -> None:
    """A row reporting neither a billed cost nor an estimate is the one shape that still
    reads PARTIAL under the new contract."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=None, estimated_cost_usd=None)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["cost"]["estimated_cost_usd"] is None
    assert detail["cost"]["cost_partial"] is True


def test_a_chunk_mixing_a_billed_and_an_estimated_row_reports_both_amounts(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=3, cost_usd=None, estimated_cost_usd=0.03)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["cost"]["cost_usd"] == pytest.approx(0.10)
    assert detail["cost"]["estimated_cost_usd"] == pytest.approx(0.03)
    assert detail["cost"]["cost_partial"] is False


def test_a_fact_without_the_estimate_key_behaves_exactly_as_today(tmp_path: Path) -> None:
    """An older runner that never sends ``estimated_cost_usd`` ingests identically to
    before the key existed — the key is additive in both directions."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    payload = _usage_payload(node_id, epoch=1, cost_usd=0.10)
    assert "estimated_cost_usd" not in payload
    payload["chunk_id"] = chunk_id
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": 2, "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["usage"][0]["estimated_cost_usd"] is None
    assert detail["cost"]["estimated_cost_usd"] is None
    assert detail["cost"]["cost_partial"] is False


def test_usage_ingest_fires_chunk_changed_over_sse(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    since = hub.events.latest_id()

    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)

    events = emitted_events(hub, since=since)
    types = [e["event"] for e in events]
    assert "chunk-changed" in types
    assert any(chunk_id in e["data"] for e in events if e["event"] == "chunk-changed")


def test_each_usage_entry_names_the_runner_that_pushed_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert [step["runner_id"] for step in detail["usage"]] == ["r1"]
