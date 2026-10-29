"""The detach/claim race is atomic (component tier), patterned on ``tests/test_stop_claim_race.py``."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from blizzard.hub.domain.execution import detach as detach_module
from tests.support import build_hub, ingest, report_lease

pytestmark = pytest.mark.component


def _claim_body(chunk_id: str, runner: str) -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a"]}


def test_a_claim_blocks_while_a_detach_holds_the_row_lock_mid_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim blocks while a detach sits between its route check and its release write."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id, "r1"))
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)

    entered_write = threading.Event()
    release_write = threading.Event()
    real_refuse_detach = detach_module.refuse_detach

    def _blocking_refuse_detach(chunk_id, route, status):  # type: ignore[no-untyped-def]
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the detach's write"
        real_refuse_detach(chunk_id, route, status)

    monkeypatch.setattr(detach_module, "refuse_detach", _blocking_refuse_detach)

    detach_result: dict[str, int] = {}

    def _detach() -> None:
        detach_result["status"] = hub.client.post(f"/api/chunks/{chunk_id}/detach").status_code

    detach_thread = threading.Thread(target=_detach)
    detach_thread.start()
    assert entered_write.wait(timeout=5), "detach never reached its (patched) write"

    claim_result: dict[str, int] = {}

    def _claim() -> None:
        claim_result["status"] = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id, "r2")).status_code

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    claim_thread.join(timeout=0.3)
    assert claim_thread.is_alive(), "the claim completed while the detach still held the row lock — not atomic"

    release_write.set()
    detach_thread.join(timeout=5)
    claim_thread.join(timeout=5)

    assert detach_result["status"] == 202, detach_result
    assert claim_result["status"] == 201, claim_result
