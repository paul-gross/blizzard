"""Concurrent requeues are atomic (component tier).

``RequeueService.requeue`` re-derives the open-escalation guard it refuses on from
``handle.facts()``, inside the same row-locked transaction its writes land under
(``bzh:store-exclusive-write``) — never from a pre-lock snapshot. Drives two concurrent
clients through a barrier, mirroring ``tests/test_claim_exactly_once.py``'s own pattern,
to expose the check-then-act gap a pre-lock read would leave open — a second requeue
racing the first must never re-release a route the first's supersession already closed."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import build_hub, ingest, report_lease

pytestmark = pytest.mark.component


def _escalated_chunk(hub, tmp_path: Path) -> str:  # type: ignore[no-untyped-def]
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)
    esc = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/escalations",
        json={"epoch": 1, "runner_id": "r1", "takeover_command": "cd env && claude --resume s"},
    )
    assert esc.status_code == 202, esc.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "needs_human"
    hub.clock.advance(timedelta(seconds=1))
    return chunk_id


def test_two_concurrent_requeues_yield_one_success_and_one_refusal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _escalated_chunk(hub, tmp_path)

    start = threading.Barrier(2)
    results: dict[str, int] = {}

    def requeue(key: str) -> None:
        start.wait()
        results[key] = hub.client.post(f"/api/chunks/{chunk_id}/requeues").status_code

    threads = [threading.Thread(target=requeue, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results.values()) == [202, 409], f"expected one success and one refusal, got {results}"
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.requeues) == 1, "a second requeue past a stale guard must not mint a second fact"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
