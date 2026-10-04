"""Exactly-once route claim under concurrency (criterion 2, component tier).

Two runners race to claim the same chunk against the real hub app; the hub must accept
exactly one — one ``201`` and one ``409`` — never two live routes. Drives two concurrent
clients through a barrier to expose any check-then-act gap."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.support import build_hub, make_ready, pointer_token

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "2"}


def _claim_body(runner: str) -> dict:
    return {"chunk_id": "", "runner_id": runner, "workspace_id": "w1", "environment_ids": [f"env-{runner}"]}


def test_two_concurrent_claims_yield_one_win_one_conflict(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)

    start = threading.Barrier(2)
    results: dict[str, int] = {}

    def claim(runner: str) -> None:
        body = _claim_body(runner) | {"chunk_id": chunk_id}
        start.wait()  # release both threads together to maximize the race
        results[runner] = hub.client.post("/api/fleet/routes", json=body).status_code

    threads = [threading.Thread(target=claim, args=(r,)) for r in ("r1", "r2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    codes = sorted(results.values())
    assert codes == [201, 409], f"expected exactly one win and one conflict, got {results}"

    # Exactly one live route persisted — the winner holds the chunk, and the board
    # shows a single running claim (never a double-claim).
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["route"] is not None
    winner = next(r for r, code in results.items() if code == 201)
    assert detail["route"]["runner_id"] == winner


def test_repeated_races_never_double_claim(tmp_path: Path) -> None:
    """Many chunks, each raced by two runners — never two winners on one chunk."""
    hub = build_hub(tmp_path)
    for i in range(8):
        pointer = {"source": "default", "ref": str(100 + i)}
        chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]}).json()["chunk_id"]
        make_ready(hub, chunk_id)
        start = threading.Barrier(2)
        codes: list[int] = []
        lock = threading.Lock()

        def claim(
            runner: str,
            cid: str = chunk_id,
            barrier: threading.Barrier = start,
            sink: list[int] = codes,
            guard: threading.Lock = lock,
        ) -> None:
            body = _claim_body(runner) | {"chunk_id": cid}
            barrier.wait()
            code = hub.client.post("/api/fleet/routes", json=body).status_code
            with guard:
                sink.append(code)

        threads = [threading.Thread(target=claim, args=(r,)) for r in ("r1", "r2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(codes) == [201, 409], f"chunk {i}: {codes}"


def test_two_hub_processes_racing_a_claim_over_one_store_yield_exactly_one_winner(tmp_path: Path) -> None:
    """The two tests above race two threads sharing one ``ClaimService`` instance — a
    single in-process lock would already serialize that. The exactly-one-wins decision
    only needs a store-level row lock (``bzh:store-exclusive-write``) once a second hub
    process serves the same store, since it shares no Python object, let alone a
    ``threading.Lock``, with the first. ``build_hub`` supports exactly this: a second call
    over the same ``tmp_path`` reopens the store the first one wrote, wiring an entirely
    separate ``HubServices`` — its own engine, its own ``ClaimService`` — mirroring two
    ``blizzard-hub host`` instances against one store."""
    first = build_hub(tmp_path)
    chunk_id = first.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "shared"})]}
    ).json()["chunk_id"]
    make_ready(first, chunk_id)
    second = build_hub(tmp_path)

    start = threading.Barrier(2)
    results: dict[str, int] = {}

    def claim(hub, runner: str) -> None:  # type: ignore[no-untyped-def]
        body = _claim_body(runner) | {"chunk_id": chunk_id}
        start.wait()  # release both processes' threads together to maximize the race
        results[runner] = hub.client.post("/api/fleet/routes", json=body).status_code

    threads = [
        threading.Thread(target=claim, args=(first, "r1")),
        threading.Thread(target=claim, args=(second, "r2")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    codes = sorted(results.values())
    assert codes == [201, 409], f"expected exactly one win and one conflict, got {results}"

    # Whichever process's client asks, the store agrees on exactly one winner.
    detail = first.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["route"] is not None
    winner = next(r for r, code in results.items() if code == 201)
    assert detail["route"]["runner_id"] == winner
    assert second.client.get(f"/api/chunks/{chunk_id}").json() == detail
