"""Exactly-once route claim under concurrency (criterion 2, component tier).

Two runners race to claim the same chunk against the **real hub app**; the hub must
accept exactly one — one ``201`` and one ``409`` — never two live routes (D-024/D-080).
The claim is the cross-machine exactly-once arbitration point (design/runner/store.md
alternatives table): sqlite's atomicity buys self-consistency, but the read-then-write
of the claim is serialized by the hub's single-writer discipline (D-023), so this test
drives two concurrent clients through a barrier to expose any check-then-act gap.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_POINTER = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/2"}


def _claim_body(runner: str) -> dict:
    return {"chunk_id": "", "runner_id": runner, "workspace_id": "w1", "environment_ids": [f"env-{runner}"]}


def test_two_concurrent_claims_yield_one_win_one_conflict(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]

    start = threading.Barrier(2)
    results: dict[str, int] = {}

    def claim(runner: str) -> None:
        body = _claim_body(runner) | {"chunk_id": chunk_id}
        start.wait()  # release both threads together to maximize the race
        results[runner] = hub.client.post("/api/routes", json=body).status_code

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
        pointer = {"provider": "github", "url": f"http://forge.local/repos/acme/widget/issues/{100 + i}"}
        chunk_id = hub.client.post("/api/chunks", json={"pointers": [pointer]}).json()["chunk_id"]
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
            code = hub.client.post("/api/routes", json=body).status_code
            with guard:
                sink.append(code)

        threads = [threading.Thread(target=claim, args=(r,)) for r in ("r1", "r2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(codes) == [201, 409], f"chunk {i}: {codes}"
