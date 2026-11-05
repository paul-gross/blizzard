"""Helpers for the analytics events api tests, shared with other test modules."""

from __future__ import annotations

from pathlib import Path

from blizzard.auth_core import Role
from tests.support.hub_request_auth import cookie, ingest_chunk
from tests.support.scaffolding import HubHarness, build_hub, seed_session, seed_user


def tool_turn(index: int, name: str, input: dict[str, object], *, timestamp: str | None = None) -> dict[str, object]:
    return {
        "index": index,
        "kind": "tool",
        "timestamp": timestamp,
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


def record_transcript(
    chunk_id: str,
    *,
    turns: list[dict[str, object]],
    node_id: str = "nd_build",
    segment_id: str = "sg_1",
    harness_id: str | None = None,
    harness_version: str | None = "claude-code-1.0",
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, object]:
    return {
        "seq": 1,
        "segment_id": segment_id,
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": 0,
        "turn_range_end": len(turns) - 1,
        "final": True,
        "normalizer_version": "claude-code-jsonl/2",
        "harness_version": harness_version,
        "harness_id": harness_id,
        "model": model,
        "effort": effort,
        "turns": turns,
    }


def seeded_hub(tmp_path: Path) -> tuple[HubHarness, str, str]:
    """A contributor session, one chunk carrying a file_read/skill_invocation/agent_spawn
    triad derived and stored, ready for the routes' own auth/filter/paging assertions."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    chunk_id = ingest_chunk(hub, headers=cookie(token))
    turns = [
        tool_turn(0, "Read", {"file_path": "src/a.py"}, timestamp="2026-08-12T09:00:00Z"),
        tool_turn(1, "Skill", {"skill": "wf-commit"}, timestamp="2026-08-12T10:00:00Z"),
        tool_turn(2, "Agent", {"subagent_type": "explorer"}, timestamp="2026-08-12T11:00:00Z"),
    ]
    push = hub.client.post(
        "/api/fleet/transcripts", json={"runner_id": "r1", "records": [record_transcript(chunk_id, turns=turns)]}
    )
    assert push.status_code == 200, push.text
    hub.hub_services.event_derivation.sweep()
    return hub, token, chunk_id
