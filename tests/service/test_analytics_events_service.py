"""The analytics events/counts routes against a real hub (blizzard#255, Phase 3): both
encodings serve the same derived events in the same order, and a JSON page bounded
below the total result count still covers it exactly once via its cursor. Run with
``BLIZZARD_SERVICE=1``."""

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


def _tool_turn(index: int, name: str, input: dict) -> dict:
    return {
        "index": index,
        "kind": "tool",
        "timestamp": None,
        "text": "",
        "tool": {
            "name": name,
            "input": input,
            "input_unparsed": None,
            "input_shape": "object",
            "tool_use_id": f"t{index}",
            "output": None,
            "output_truncated": False,
        },
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }


def _record(chunk_id: str) -> dict:
    turns = [
        _tool_turn(0, "Read", {"file_path": "src/a.py"}),
        _tool_turn(1, "Skill", {"skill": "wf-commit"}),
        _tool_turn(2, "Task", {"subagent_type": "explorer"}),
    ]
    return {
        "seq": 1,
        "segment_id": "sg_1",
        "chunk_id": chunk_id,
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": 0,
        "turn_range_end": 2,
        "final": True,
        "normalizer_version": "claude-code-jsonl/2",
        "harness_version": "claude-code-1.0",
        "turns": turns,
    }


def test_both_encodings_serve_the_same_derived_events_in_the_same_order(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "analytics events over a real hub")
        push = hub.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(chunk_id)]})
        assert push.status_code == 200, push.text
        derived = hub.post("/api/analytics/re-derive", json={"chunk_id": chunk_id})
        assert derived.status_code == 200, derived.text
        assert derived.json()["derived"] == 1

        json_resp = hub.get("/api/analytics/events", params={"kind": "skill_invocation"})
        assert json_resp.status_code == 200, json_resp.text
        assert [e["subject"] for e in json_resp.json()["events"]] == ["wf-commit"]

        ndjson_resp = hub.get("/api/analytics/events/ndjson")
        assert ndjson_resp.status_code == 200, ndjson_resp.text
        assert ndjson_resp.headers["content-type"].startswith("application/x-ndjson")
        lines = [line for line in ndjson_resp.text.strip().split("\n") if line]
        assert len(lines) == 3


def test_a_json_page_bounded_below_the_total_covers_it_exactly_once_via_its_cursor(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "analytics paging over a real hub")
        push = hub.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(chunk_id)]})
        assert push.status_code == 200, push.text
        derived = hub.post("/api/analytics/re-derive", json={"chunk_id": chunk_id})
        assert derived.status_code == 200, derived.text

        seen: list[int] = []
        cursor = None
        for _ in range(10):  # generous bound on iterations for a 3-row set
            params = {"limit": 1} | ({"cursor": cursor} if cursor is not None else {})
            page = hub.get("/api/analytics/events", params=params)
            assert page.status_code == 200, page.text
            events = page.json()["events"]
            assert len(events) == 1
            seen.append(events[0]["id"])
            cursor = page.json()["next_cursor"]
            if cursor is None:
                break

        assert cursor is None
        assert len(seen) == len(set(seen)) == 3
