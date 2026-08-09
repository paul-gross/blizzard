"""Transcript-segment service tier (blizzard#247) — the real hub's fleet ingest route
driven from outside a running daemon. No runner-side counterpart exists yet (#246), so
this drives ``POST /api/fleet/transcripts`` directly rather than through a mock-runner
``/_drive/*`` verb. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _forge, _free_port, _hub
from tests.service.support import mint_fixture, require_mock_fleet, require_winter_source, service_gate

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
    return {
        "seq": seq,
        "segment_id": "sg_1",
        "chunk_id": chunk_id,
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": 0,
        "turn_range_end": 0,
        "final": True,
        "normalizer_version": "v1",
        "harness_version": "claude-code-1.0",
        "turns": [
            {
                "index": 0,
                "kind": "asst",
                "timestamp": None,
                "text": "hi",
                "tool": None,
                "thinking_redacted": False,
                "sidechain": None,
                "truncated": False,
            }
        ],
    }


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
