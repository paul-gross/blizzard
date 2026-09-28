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


def test_a_stop_racing_a_completion_is_refused_once_the_completion_wins(tmp_path: Path) -> None:
    """Whichever wins the row lock first commits its own terminal fact; a stop that loses
    to a completion re-derives status under the lock, sees it, and is refused."""
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
        results["complete"] = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"}).status_code

    threads = [threading.Thread(target=stop), threading.Thread(target=complete)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    # `complete` is admitted from `stopped` (any non-`done` status), so stop-then-complete
    # legitimately lands both facts. What the row lock must rule out is a stop landing
    # *after* a completion: a `409` stop means the completion won, and only it landed.
    if results["stop"] == 409:
        assert facts.operator_completed and not facts.stopped, (
            f"the stop was refused yet landed its own fact past a completion: {facts}"
        )
    else:
        assert results["stop"] == 202 and facts.stopped
