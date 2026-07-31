"""``POST /api/fleet/chunks/{id}/pause`` and ``.../resume`` (issue #185).

The runner machine panel's Pause/Resume proxy target
(:mod:`blizzard.runner.api.chunk_detail`) delegates onto the very same
:func:`blizzard.hub.api.chunks.pause_chunk`/``resume_chunk`` the board's own
route calls (``canon:one-owner`` — no duplicated pause logic), the same
delegation shape as ``get_chunk``/``get_work_items`` in
:mod:`blizzard.hub.api.fleet`. The domain refusal itself
(:class:`~blizzard.hub.domain.pause.ChunkNotPausable`) is exercised end to end
by ``test_chunks_api.py``; this file proves only that the fleet route reaches
it, with ``by`` defaulting to ``operator`` exactly as the board's own mutation
sends it explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, ingest

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}


def _routed_chunk(hub) -> str:  # type: ignore[no-untyped-def]
    chunk_id = ingest(hub, [_POINTER])
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    return chunk_id


def test_fleet_pause_delegates_and_defaults_by_to_operator(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _routed_chunk(hub)

    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/pause")

    assert resp.status_code == 202, resp.text
    assert resp.json()["chunk_id"] == chunk_id
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "paused"
    assert detail["pause"]["by"] == "operator"


def test_fleet_resume_delegates_and_clears_the_pause(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _routed_chunk(hub)
    assert hub.client.post(f"/api/fleet/chunks/{chunk_id}/pause").status_code == 202

    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/resume")

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["pause"] is None


def test_fleet_pause_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/fleet/chunks/ch_nope/pause")

    assert resp.status_code == 404


def test_fleet_resume_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/fleet/chunks/ch_nope/resume")

    assert resp.status_code == 404
