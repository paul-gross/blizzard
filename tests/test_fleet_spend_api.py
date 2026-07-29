"""``GET /api/spend`` — the fleet-wide spend-since read (issue #60, component tier).

A small dedicated read distinct from a chunk's own derived total
(``tests/test_usage_facts_ingest.py``): it sums usage facts **across every chunk**,
filtered by ``recorded_at >= since`` rather than by chunk id — derived at read time,
never a stored column (``bzh:facts-not-status``). This file proves: the fleet-wide sum
spans multiple chunks, the ``since`` cutoff excludes facts recorded before it, the
cost-absent lower-bound + PARTIAL flag, and a malformed ``since`` 422s.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.foundation.store.utc import iso_utc
from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER_A = {"source": "default", "ref": "7"}
_POINTER_B = {"source": "default", "ref": "8"}


def _claim(hub, pointer: dict) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]}).json()["chunk_id"]
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    return chunk_id, node_id


def _push_usage(hub, *, chunk_id: str, node_id: str, epoch: int, seq: int, cost_usd: float | None) -> None:  # type: ignore[no-untyped-def]
    payload = {
        "chunk_id": chunk_id,
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
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": seq, "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text


def test_fleet_spend_sums_usage_across_every_chunk_since_the_cutoff(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_a, node_a = _claim(hub, _POINTER_A)
    chunk_b, node_b = _claim(hub, _POINTER_B)
    report_lease(hub, chunk_a, epoch=1, seq=1)
    report_lease(hub, chunk_b, epoch=1, seq=2)

    # Before the cutoff — must not be counted.
    _push_usage(hub, chunk_id=chunk_a, node_id=node_a, epoch=1, seq=3, cost_usd=1.00)

    hub.clock.advance(timedelta(hours=1))
    cutoff = iso_utc(hub.clock.now())

    # At/after the cutoff — from two different chunks, both counted.
    _push_usage(hub, chunk_id=chunk_a, node_id=node_a, epoch=1, seq=4, cost_usd=0.25)
    _push_usage(hub, chunk_id=chunk_b, node_id=node_b, epoch=1, seq=5, cost_usd=0.50)

    resp = hub.client.get("/api/spend", params={"since": cutoff})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["since"] == cutoff
    assert body["input_tokens"] == 200  # two rows, not the excluded earlier one
    assert body["output_tokens"] == 100
    assert body["cost_usd"] == pytest.approx(0.75)
    assert body["cost_partial"] is False


def test_fleet_spend_flags_partial_when_any_summed_row_has_no_cost(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub, _POINTER_A)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    cutoff = iso_utc(hub.clock.now())

    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=3, cost_usd=None)

    resp = hub.client.get("/api/spend", params={"since": cutoff})
    body = resp.json()
    assert body["input_tokens"] == 200  # tokens still summed for both rows
    assert body["cost_usd"] == pytest.approx(0.10)  # the lower bound
    assert body["cost_partial"] is True


def test_fleet_spend_rejects_a_malformed_since(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/spend", params={"since": "not-a-timestamp"})

    assert resp.status_code == 422, resp.text


def test_fleet_spend_omitting_until_is_byte_identical_to_before(tmp_path: Path) -> None:
    # Same shape as test_fleet_spend_sums_usage_across_every_chunk_since_the_cutoff
    # above, pinned again here beside the new `until` field so a regression that
    # narrows the default (unbounded) window shows up next to the tests that would
    # otherwise carry that proof alone.
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub, _POINTER_A)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    cutoff = iso_utc(hub.clock.now())
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)
    hub.clock.advance(timedelta(hours=1))
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=3, cost_usd=0.20)

    body = hub.client.get("/api/spend", params={"since": cutoff}).json()

    assert body["until"] is None
    assert body["input_tokens"] == 200  # both rows, no upper bound to exclude the later one
    assert body["cost_usd"] == pytest.approx(0.30)


def test_fleet_spend_until_excludes_a_fact_recorded_at_or_after_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claim(hub, _POINTER_A)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    since = iso_utc(hub.clock.now())
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.10)

    hub.clock.advance(timedelta(hours=1))
    until = iso_utc(hub.clock.now())

    # Recorded exactly at `until` — excluded, the same boundary rule `since` gets
    # the inclusive half of.
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=3, cost_usd=0.20)

    resp = hub.client.get("/api/spend", params={"since": since, "until": until})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["until"] == until
    assert body["input_tokens"] == 100  # one row, not the one recorded at `until`
    assert body["cost_usd"] == pytest.approx(0.10)


def test_fleet_spend_rejects_a_malformed_until(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/spend", params={"since": iso_utc(hub.clock.now()), "until": "not-a-timestamp"})

    assert resp.status_code == 422, resp.text
    assert "until" in resp.json()["detail"]
