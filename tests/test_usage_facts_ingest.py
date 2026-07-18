"""Usage facts land at the hub, idempotent, stale-epoch attributed (component tier).

Usage rides the same store-and-forward rails as ``lease.minted`` (``POST /events``,
per-runner seq high-water idempotency, ``tests/test_store_and_forward.py``'s
established pattern) but is deliberately **not** epoch-fenced: a row whose epoch
trails the chunk's latest is real spend by a fenced-out zombie attempt and must be
recorded and attributed to its own epoch, never dropped (contrast the completion
path's epoch fence, ``tests/test_store_and_forward.py``'s reflush test). This file
proves: idempotent ingest (a replayed seq lands nothing twice), stale-epoch usage
still lands, per-node-step usage + the derived chunk total on ``GET /chunks/{id}``,
the derived total on ``GET /chunks``, and the ``chunk-changed`` SSE re-broadcast.
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
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    return chunk_id, node_id


def _usage_payload(node_id: str, *, epoch: int, cost_usd: float | None) -> dict:
    return {
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


def _push_usage(hub, *, chunk_id: str, node_id: str, epoch: int, seq: int, cost_usd: float | None = 0.1) -> dict:  # type: ignore[no-untyped-def]
    payload = _usage_payload(node_id, epoch=epoch, cost_usd=cost_usd)
    payload["chunk_id"] = chunk_id
    resp = hub.client.post(
        "/api/events",
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

    listing = hub.client.get("/api/chunks").json()
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
