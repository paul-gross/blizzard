"""``GET /api/fleet/summary`` — the runner-panel fleet-pulse read (issue #76, component tier).

The fleet router's counterpart to the runner-local pass-through
(``tests/test_fleet_summary_proxy.py`` covers the runner half). It folds every chunk's
**derived** status into the four bucket counts the machine panel's strip shows — never a
stored column (``bzh:facts-not-status``). The fold itself is unit-tested exhaustively in
``tests/test_fleet_summary_derivation.py``; this file proves the *route* wires the live
derivation to the wire model: an empty fleet is all-zeros, and a live fleet's ready and
running chunks land in their buckets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, ingest

pytestmark = pytest.mark.component


def _claim_route(hub, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    """Claim a route for the chunk — a live route derives ``running``."""
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert resp.status_code == 201, resp.text


def test_empty_fleet_summary_is_all_zeros(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/fleet/summary")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ready": 0, "running": 0, "waiting": 0, "needs": 0}


def test_summary_folds_a_live_fleet_into_buckets(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # Two ready chunks (ingested + promoted, no route) and one running (route claimed).
    ingest(hub, [{"source": "default", "ref": "1"}])
    ingest(hub, [{"source": "default", "ref": "2"}])
    running = ingest(hub, [{"source": "default", "ref": "3"}])
    _claim_route(hub, running)

    resp = hub.client.get("/api/fleet/summary")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ready": 2, "running": 1, "waiting": 0, "needs": 0}


def test_summary_is_a_fixed_four_integers_regardless_of_fleet_size(tmp_path: Path) -> None:
    # The payload is four counts, never the chunk list — the strip needs a pulse, not
    # a per-chunk read that grows with the fleet.
    hub = build_hub(tmp_path)
    for ref in range(5):
        ingest(hub, [{"source": "default", "ref": str(ref)}])
    body = hub.client.get("/api/fleet/summary").json()
    assert set(body) == {"ready", "running", "waiting", "needs"}
    assert body["ready"] == 5
