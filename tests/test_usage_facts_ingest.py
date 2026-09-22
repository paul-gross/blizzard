"""Usage facts land at the hub, idempotent, stale-epoch attributed (component tier).

Usage rides the same store-and-forward rails as ``lease.minted`` but is deliberately
NOT epoch-fenced: a stale-epoch row is real spend by a fenced-out zombie attempt and
must land, attributed to its own epoch. Proves idempotent ingest, totals, and SSE.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, emitted_events, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}


def _claim(hub) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    return chunk_id, node_id


def _usage_payload(
    node_id: str, *, epoch: int, cost_usd: float | None, estimated_cost_usd: float | None = None
) -> dict:
    payload = {
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
    hub,  # type: ignore[no-untyped-def]
    *,
    chunk_id: str,
    node_id: str,
    epoch: int,
    seq: int,
    cost_usd: float | None = 0.1,
    estimated_cost_usd: float | None = None,
) -> dict:
    payload = _usage_payload(node_id, epoch=epoch, cost_usd=cost_usd, estimated_cost_usd=estimated_cost_usd)
    payload["chunk_id"] = chunk_id
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": seq, "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


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
    """The mark is written after every applied fact, not once after the whole batch: a crash
    partway through a multi-fact push leaves it at the last fact whose write actually landed,
    bounding a lost-ack replay to one fact regardless of how many rode in the same batch."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    real_record_usage = hub.services.facts._usage.record_usage
    calls = {"n": 0}

    def _crash_on_second_call(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash mid-batch")
        return real_record_usage(*args, **kwargs)

    hub.services.facts._usage.record_usage = _crash_on_second_call  # type: ignore[method-assign]
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
        hub.services.facts._usage.record_usage = real_record_usage  # type: ignore[method-assign]

    # Seq 2's write and mark both landed before the crash; seq 3 raised before its own mark
    # write, and seq 4 was never attempted — so seq 2 replays as already_applied and seq 3 applies fresh.
    replay = _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)
    assert replay["applied"] == [] and replay["already_applied"] == [2]

    resume = _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=3, cost_usd=0.20)
    assert resume["applied"] == [3]

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert len(detail["usage"]) == 2  # seq 2 and seq 3's rows only — seq 4 never landed


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
