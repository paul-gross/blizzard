"""Helpers for the runner harness opencode transcript tests, shared with other test modules."""

from __future__ import annotations

import json
from typing import Any

import structlog

from blizzard.runner.harness.opencode.transcript.export import OpenCodeExportError
from blizzard.runner.harness.transcript import TranscriptErrorFactory


def error_factory() -> TranscriptErrorFactory:
    return TranscriptErrorFactory(structlog.get_logger("test"))


class FakeExporter:
    """A scripted :class:`IOpenCodeExporter`: each session id maps to a JSON string to
    return, or an exception instance to raise — mutable, so a test can grow a session
    between two reads the way a live ``opencode export`` would."""

    def __init__(self, scripts: dict[str, str | Exception] | None = None) -> None:
        self.scripts = dict(scripts or {})
        self.calls: list[str] = []

    def export(self, session_id: str) -> str:
        self.calls.append(session_id)
        script = self.scripts.get(session_id)
        if script is None:
            raise OpenCodeExportError(f"no script for {session_id!r}")
        if isinstance(script, Exception):
            raise script
        return script


def _session_info(session_id: str, *, version: str = "1.18.25", parent_id: str | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {"id": session_id, "version": version}
    if parent_id is not None:
        info["parentID"] = parent_id
    return info


def tool_part(
    session_id: str,
    message_id: str,
    part_id: str,
    *,
    call_id: str,
    tool: str = "bash",
    status: str = "completed",
    input: dict[str, Any] | None = None,
    output: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {"status": status, "input": input or {}}
    if output is not None:
        state["output"] = output
    if error is not None:
        state["error"] = error
    if metadata is not None:
        state["metadata"] = metadata
    if title is not None:
        state["title"] = title
    return {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "tool",
        "callID": call_id,
        "tool": tool,
        "state": state,
    }


def user_message(session_id: str, message_id: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"info": {"id": message_id, "sessionID": session_id, "role": "user"}, "parts": parts}


def assistant_message(session_id: str, message_id: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"info": {"id": message_id, "sessionID": session_id, "role": "assistant"}, "parts": parts}


def opencode_export(session_id: str, messages: list[dict[str, Any]], **info_kwargs: Any) -> str:
    return json.dumps({"info": _session_info(session_id, **info_kwargs), "messages": messages})
