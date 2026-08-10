"""Transcript-segment service tier (blizzard#247, blizzard#249) — the real hub's fleet
ingest and lease-transcript-read routes driven from outside a running daemon. No
runner-side counterpart exists yet (#246), so this drives ``POST /api/fleet/transcripts``
and ``GET .../transcript-segments`` directly rather than through a mock-runner
``/_drive/*`` verb. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.transcripts import RECORD_MAX_BYTES
from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _forge, _free_port, _hub
from tests.service.support import (
    mint_fixture,
    mock_hub,
    mock_hub_chunk_spec,
    require_mock_fleet,
    require_winter_source,
    service_gate,
    transcript_segment_record,
)

pytestmark = [pytest.mark.service, service_gate]


def _stack(tmp_path: Path):  # type: ignore[no-untyped-def]
    bin_dir = require_mock_fleet()
    _workspace, origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    forge_port, hub_port = _free_port(), _free_port()
    return bin_dir, origins, forge_port, hub_port


def _ingest(forge, hub, title: str) -> str:  # type: ignore[no-untyped-def]
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    return str(ingested.json()["chunk_id"])


def _record(chunk_id: str, *, seq: int) -> dict:
    return transcript_segment_record(chunk_id, seq=seq)


def test_ingest_and_read_back_round_trip_over_the_wire(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "transcript segments")

        ack = hub.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(chunk_id, seq=1)]})
        assert ack.status_code == 200, ack.text
        assert ack.json()["applied"] == [1]

        index = hub.get(f"/api/chunks/{chunk_id}/transcripts")
        assert index.status_code == 200, index.text
        [entry] = index.json()["segments"]
        assert entry["segment_id"] == "sg_1"
        assert entry["final"] is True

        content = hub.get(f"/api/chunks/{chunk_id}/transcripts/sg_1")
        assert content.status_code == 200, content.text
        assert [t["text"] for t in content.json()["turns"]] == ["hi"]


def test_a_segment_is_not_readable_through_another_chunks_path(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        owning = _ingest(forge, hub, "owning chunk")
        other = _ingest(forge, hub, "other chunk")
        ack = hub.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(owning, seq=1)]})
        assert ack.status_code == 200, ack.text

        assert hub.get(f"/api/chunks/{other}/transcripts/sg_1").status_code == 404
        assert hub.get(f"/api/chunks/{owning}/transcripts/sg_1").status_code == 200


def test_a_cap_rejected_record_re_offered_under_a_fresh_seq_is_re_adjudicated(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "re-adjudicated segment")
        oversized = _record(chunk_id, seq=1)
        oversized["turns"][0]["text"] = "x" * (RECORD_MAX_BYTES + 1)

        ack = hub.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [oversized]})
        assert ack.status_code == 200, ack.text
        assert ack.json()["capped"] == [1]
        [entry] = hub.get(f"/api/chunks/{chunk_id}/transcripts").json()["segments"]
        assert entry["truncated"] is True

        retry = hub.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(chunk_id, seq=99)]})
        assert retry.json()["applied"] == [99], retry.text
        content = hub.get(f"/api/chunks/{chunk_id}/transcripts/sg_1")
        assert [t["text"] for t in content.json()["turns"]] == ["hi"]
        [entry] = hub.get(f"/api/chunks/{chunk_id}/transcripts").json()["segments"]
        assert entry["truncated"] is False


# --- the lease-transcript read route (D2/D3, issue #249) ------------------------


def test_an_enrolled_runner_reads_back_the_lease_segments_it_shipped(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "lease transcript read")
        register = hub.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws-1"})
        assert register.status_code == 201, register.text
        enroll = hub.post("/api/runners/r1/enrollments")
        assert enroll.status_code == 201, enroll.text
        token = enroll.json()["token"]

        ack = hub.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(chunk_id, seq=1)]})
        assert ack.status_code == 200, ack.text

        resp = hub.get(
            f"/api/fleet/chunks/{chunk_id}/transcript-segments",
            params={"node_id": "nd_build", "epoch": 1},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["chunk_id"] == chunk_id
        assert body["node_id"] == "nd_build"
        assert body["epoch"] == 1
        assert body["truncated"] is False
        assert [t["text"] for t in body["turns"]] == ["hi"]


def test_the_mock_hubs_counterpart_route_round_trips_a_shipped_lease(tmp_path: Path) -> None:
    """D6: the mock hub's retention and its own counterpart route, driven from the app
    side over real HTTP against a real ``blizzard-mock-hub`` subprocess."""
    bin_dir = require_mock_fleet()
    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(f"{REPO}/issues/1"))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        ack = hub.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(chunk_id, seq=1)]})
        assert ack.status_code == 200, ack.text

        resp = hub.get(f"/api/fleet/chunks/{chunk_id}/transcript-segments", params={"node_id": "nd_build", "epoch": 1})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["chunk_id"] == chunk_id
        assert [t["text"] for t in body["turns"]] == ["hi"]
