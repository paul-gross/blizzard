"""Transcript-segment service tier — the mock hub's counterpart to the lease-transcript
read route, driven over real HTTP. The real hub's routes live in
``tests/service/hub/transcript-segments.spec.ts``. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fleet_launchers import REPO
from tests.mock_fleet_support import (
    mock_hub,
    mock_hub_chunk_spec,
    require_mock_fleet,
    service_gate,
    transcript_segment_record,
)
from tests.runner_join import fleet_headers
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]


def _record(chunk_id: str, *, seq: int) -> dict:
    return transcript_segment_record(chunk_id, seq=seq)


def test_the_mock_hubs_counterpart_route_round_trips_a_shipped_lease(tmp_path: Path) -> None:
    """The mock hub's retention and its own counterpart route, driven from the app
    side over real HTTP against a real ``blizzard-mock-hub`` subprocess."""
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(f"{REPO}/issues/1"))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        ack = hub.post(
            "/api/fleet/transcripts", json={"records": [_record(chunk_id, seq=1)]}, headers=fleet_headers(hub)
        )
        assert ack.status_code == 200, ack.text

        resp = hub.get(
            f"/api/fleet/chunks/{chunk_id}/transcript-segments",
            params={"node_id": "nd_build", "epoch": 1},
            headers=fleet_headers(hub),
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["chunk_id"] == chunk_id
        assert [t["text"] for t in body["turns"]] == ["hi"]
