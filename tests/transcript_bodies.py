"""Wire-shaped transcript bodies a CLI test feeds its renderer or answers a route with."""

from __future__ import annotations

from typing import Any


def turn(index: int, kind: str, text: str = "", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "index": index,
        "kind": kind,
        "timestamp": f"2026-10-09T12:00:{index:02d}Z",
        "text": text,
        "tool": None,
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }
    return base | over


def tool(name: str, tool_use_id: str | None, output: str | None, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "input": {"command": "ls"},
        "input_unparsed": None,
        "input_shape": "object",
        "tool_use_id": tool_use_id,
        "output": output,
        "output_truncated": False,
        "input_truncated": False,
        "output_patch": False,
    }
    return base | over


def index_body(chunk_id: str = "ch_1", *segment_ids: str) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "segments": [
            {
                "segment_id": segment_id,
                "node_id": "build",
                "epoch": 1,
                "spawn_generation": 0,
                "turn_range_start": 0,
                "turn_range_end": 4,
                "final": True,
                "truncated": False,
                "byte_count": 321,
                "harness_id": "claude-code",
                "normalizer_version": "1",
                "harness_version": None,
                "received_at": "2026-10-09T12:05:00Z",
            }
            for segment_id in segment_ids
        ],
    }


def content_body(segment_id: str = "sg_1") -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "final": True,
        "truncated": False,
        "turns": [
            turn(0, "env", "you are a worker"),
            turn(1, "asst", "listing the files"),
            turn(2, "thinking", "", thinking_redacted=True),
            turn(3, "tool", "", tool=tool("Bash", "tu_1", None)),
            turn(4, "tool", "", tool=tool("Bash", "tu_1", "a.txt\nb.txt", output_patch=True)),
        ],
    }
