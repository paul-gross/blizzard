"""Transcript-event re-derive service tier (blizzard#254 D7) — ``POST
/api/analytics/re-derive`` against a real hub: a segment-scoped force, and a
chunk-scoped, bounded call that converges to ``remaining=0`` over repeated calls. Run
with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _forge, _free_port, _hub
from tests.service.support import (
    mint_fixture,
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


def _record(chunk_id: str, *, segment_id: str, seq: int) -> dict:
    record = transcript_segment_record(chunk_id, seq=seq)
    record["segment_id"] = segment_id
    return record


def test_a_segment_scoped_call_forces_that_one_segment(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "re-derive segment scope")
        ack = hub.post(
            "/api/fleet/transcripts",
            json={"runner_id": "r1", "records": [_record(chunk_id, segment_id="sg_1", seq=1)]},
        )
        assert ack.status_code == 200, ack.text

        resp = hub.post("/api/analytics/re-derive", json={"segment_id": "sg_1"})

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"derived": 1, "remaining": 0}


def test_a_chunk_scoped_bounded_call_converges_over_repeated_calls(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "re-derive chunk scope")
        records = [_record(chunk_id, segment_id=f"sg_{i}", seq=i) for i in range(1, 4)]
        ack = hub.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": records})
        assert ack.status_code == 200, ack.text

        first = hub.post("/api/analytics/re-derive", json={"chunk_id": chunk_id, "limit": 2})
        assert first.status_code == 200, first.text
        assert first.json() == {"derived": 2, "remaining": 1}

        second = hub.post("/api/analytics/re-derive", json={"chunk_id": chunk_id, "limit": 2})
        assert second.status_code == 200, second.text
        assert second.json() == {"derived": 1, "remaining": 0}

        third = hub.post("/api/analytics/re-derive", json={"chunk_id": chunk_id, "limit": 2})
        assert third.status_code == 200, third.text
        assert third.json() == {"derived": 0, "remaining": 0}


def test_a_call_naming_both_a_segment_and_a_chunk_is_422(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "re-derive mutually exclusive scope")

        resp = hub.post("/api/analytics/re-derive", json={"segment_id": "sg_1", "chunk_id": chunk_id})

        assert resp.status_code == 422, resp.text
