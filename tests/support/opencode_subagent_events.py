"""Helpers for the runner harness opencode subagents tests, shared with other test modules."""

from __future__ import annotations

import json
from typing import Any


def step(part_id: str, session: str, message: str, tokens: int = 10, cost: float = 0) -> dict[str, Any]:
    return {
        "id": part_id,
        "sessionID": session,
        "messageID": message,
        "type": "step-finish",
        "reason": "stop",
        "cost": cost,
        "tokens": {"input": tokens, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
    }


def opencode_task(
    part_id: str,
    session: str,
    message: str,
    child: str,
    start: int,
    end: int | None,
) -> dict[str, Any]:
    time: dict[str, int] = {"start": start} if end is None else {"start": start, "end": end}
    return {
        "id": part_id,
        "sessionID": session,
        "messageID": message,
        "type": "tool",
        "callID": f"call_{part_id}",
        "tool": "task",
        "state": {
            "status": "completed" if end is not None else "running",
            "input": {"subagent_type": "x"},
            "output": "ok",
            "time": time,
            "metadata": {"sessionId": child},
        },
    }


def opencode_session(
    session: str, parent: str | None, messages: list[tuple[str, int, str, list[dict[str, Any]]]]
) -> str:
    info: dict[str, Any] = {"id": session}
    if parent is not None:
        info["parentID"] = parent
    return json.dumps(
        {
            "info": info,
            "messages": [
                {
                    "info": {
                        "id": mid,
                        "sessionID": session,
                        "role": "assistant",
                        "providerID": "openai",
                        "modelID": model,
                        "time": {"created": created},
                    },
                    "parts": parts,
                }
                for mid, created, model, parts in messages
            ],
        }
    )


def opencode_events(parts: list[dict[str, Any]], *, root: str = "root", stamp: int = 1_000) -> str:
    return "\n".join(
        json.dumps(
            {
                "type": "tool_use" if p["type"] == "tool" else "step_finish",
                "sessionID": root,
                "timestamp": stamp,
                "part": p,
            }
        )
        for p in parts
    )
