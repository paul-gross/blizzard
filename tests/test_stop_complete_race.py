"""Concurrent stop/complete refusals are atomic (component tier).

``StopService.stop``/``CompleteService.complete`` re-derive the terminal-status guard
they refuse on from ``handle.facts()``, inside the same row-locked transaction the write
lands under (``bzh:store-exclusive-write``) — never from a pre-lock snapshot. Drives two
concurrent clients through a barrier, mirroring ``tests/test_claim_exactly_once.py``'s own
pattern, to expose the check-then-act gap a pre-lock read would leave open."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.support import build_hub, ingest, report_lease

pytestmark = pytest.mark.component


def _claim_and_lease(hub, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)


def test_two_concurrent_stops_yield_one_success_and_one_refusal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    _claim_and_lease(hub, chunk_id)

    start = threading.Barrier(2)
    results: dict[str, int] = {}

    def stop(key: str) -> None:
        start.wait()
        results[key] = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code

    threads = [threading.Thread(target=stop, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results.values()) == [202, 409], f"expected one success and one refusal, got {results}"
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None and facts.stopped, "the winner's fact must have landed"


def test_two_concurrent_completions_write_only_one_fact(tmp_path: Path) -> None:
    """Completion is idempotent-by-no-op rather than refused, so both calls answer
    ``202`` — the guard this proves is that only one ``chunk_completed`` fact lands, not
    a duplicate from a second caller whose pre-lock read predates the first's commit."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    _claim_and_lease(hub, chunk_id)

    start = threading.Barrier(2)
    results: dict[str, int] = {}

    def complete(key: str) -> None:
        start.wait()
        results[key] = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"}).status_code

    threads = [threading.Thread(target=complete, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert list(results.values()) == [202, 202], results
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_a_stop_racing_a_completion_never_lands_both_terminal_facts(tmp_path: Path) -> None:
    """Whichever wins the row lock first commits its own terminal fact; the second,
    re-deriving status fresh under the same lock, must see the winner's fact and never
    land its own alongside it."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    _claim_and_lease(hub, chunk_id)

    start = threading.Barrier(2)
    results: dict[str, int] = {}

    def stop() -> None:
        start.wait()
        results["stop"] = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code

    def complete() -> None:
        start.wait()
        results["complete"] = hub.client.post(
            f"/api/chunks/{chunk_id}/complete", json={"by": "operator"}
        ).status_code

    threads = [threading.Thread(target=stop), threading.Thread(target=complete)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    # `complete` never refuses (idempotent no-op), so `stop`'s own outcome alone tells
    # apart the two orderings: it wins (202, and only `stopped` landed) or loses to a
    # completion that got there first (409, and only `operator_completed` landed).
    assert not (facts.stopped and facts.operator_completed), (
        f"both terminal facts landed — the loser wrote past a stale guard: {facts}"
    )
    if results["stop"] == 202:
        assert facts.stopped and not facts.operator_completed
    else:
        assert results["stop"] == 409
        assert facts.operator_completed and not facts.stopped
