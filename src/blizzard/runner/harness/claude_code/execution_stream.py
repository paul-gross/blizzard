"""A Claude Code worker's execution evidence, read off its own per-generation ``stream-json`` stdout.

A worker launches with ``-p --output-format stream-json --verbose``, so its stdout file is one JSON event per line, and
every write to that file comes from that generation's own process. The lines carry no wall-clock time, so the file's
mtime is when the evidence was produced. A running tool keeps the file written: Claude Code emits a ``tool_progress``
heartbeat every :data:`HEARTBEAT_PERIOD` for a foreground tool, and for a foreground subagent the parent's own ``Agent``
call heartbeats for as long as the subagent runs. The recorded streams are
``src/blizzard/runner/harness/contracts/claude_code/<version>/``."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from packaging.version import InvalidVersion, Version

from blizzard.foundation.roles import adapter_model
from blizzard.runner.harness.json_value import JsonObject, decode_json
from blizzard.runner.harness.observation import ExecutionActivity, ExecutionLiveness, ExecutionObservation

__all__ = [
    "EVIDENCE_FRESHNESS",
    "HEARTBEAT_MIN_VERSION",
    "HEARTBEAT_PERIOD",
    "StreamSnapshot",
    "observe_stream",
    "read_stream_snapshot",
]

# How often Claude Code writes a `tool_progress` heartbeat for a running tool, as recorded.
HEARTBEAT_PERIOD = timedelta(seconds=30)

# A write older than this is no longer evidence the execution is running: three missed heartbeats.
EVIDENCE_FRESHNESS = 3 * HEARTBEAT_PERIOD

# The oldest version recorded emitting tool heartbeats. An older version is not observed at all: a long tool there
# would write nothing, and its silence would read the same as a hang.
HEARTBEAT_MIN_VERSION = Version("2.1.322")

# The bounded reads: the stream's first line names the version, its tail holds the newest event.
_HEAD_BYTES = 256 * 1024
_TAIL_BYTES = 64 * 1024

# Events that say a tool call is outstanding: its heartbeat, and the task lifecycle a running tool reports.
_TOOL_TASK_SUBTYPES = frozenset({"task_started", "task_progress", "task_updated"})


@adapter_model
@dataclass(frozen=True)
class StreamSnapshot:
    """One bounded look at a stdout file: its first line, its tail, and when it was last written."""

    head: str
    tail: str
    written_at: datetime


def read_stream_snapshot(path: str) -> StreamSnapshot | None:
    """``path``'s first line, tail, and mtime, or ``None`` when it is unset, absent, or unreadable."""
    if not path:
        return None
    try:
        with open(path, "rb") as stream:
            written_at = datetime.fromtimestamp(os.fstat(stream.fileno()).st_mtime, tz=UTC)
            head = stream.readline(_HEAD_BYTES)
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - _TAIL_BYTES))
            tail = stream.read(_TAIL_BYTES)
    except OSError:
        return None
    return StreamSnapshot(
        head=head.decode("utf-8", errors="replace"),
        tail=tail.decode("utf-8", errors="replace"),
        written_at=written_at,
    )


def observe_stream(snapshot: StreamSnapshot | None, now: datetime) -> ExecutionObservation:
    """What one generation's stream shows of its execution: ``exited`` after its ``result``, ``alive`` while it was
    written within :data:`EVIDENCE_FRESHNESS` of ``now``, otherwise ``unknown``."""
    if snapshot is None:
        return ExecutionObservation.unknown("claude-code stdout missing or unreadable")
    init = _decoded(snapshot.head)
    if init is None or init.get("type") != "system" or init.get("subtype") != "init":
        return ExecutionObservation.unknown("claude-code stdout carries no stream-json init event")
    version = _version(init)
    if version is None:
        return ExecutionObservation.unknown("claude-code stream names no version")
    if version < HEARTBEAT_MIN_VERSION:
        return ExecutionObservation.unknown(
            f"claude-code {version} predates tool heartbeats ({HEARTBEAT_MIN_VERSION}); execution not observed"
        )
    newest = _newest_events(snapshot.tail)
    if not newest:
        return ExecutionObservation.unknown("claude-code stdout tail holds no complete event")
    if newest[0].get("type") == "result":
        return ExecutionObservation(
            liveness=ExecutionLiveness.EXITED,
            activity=ExecutionActivity.UNKNOWN,
            evidence_at=snapshot.written_at,
            diagnostic="claude-code result written",
        )
    age = now - snapshot.written_at
    if age > EVIDENCE_FRESHNESS:
        return ExecutionObservation.unknown(f"claude-code stdout last written {int(age.total_seconds())}s ago")
    activity = _activity(newest)
    return ExecutionObservation(
        liveness=ExecutionLiveness.ALIVE,
        activity=activity,
        evidence_at=snapshot.written_at,
        diagnostic=f"claude-code {version} stream written; {activity}",
    )


def _decoded(line: str) -> JsonObject | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        decoded = decode_json(stripped)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _version(init: JsonObject) -> Version | None:
    raw = init.get("claude_code_version")
    if not isinstance(raw, str):
        return None
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def _newest_events(tail: str) -> list[JsonObject]:
    """The tail's complete events, newest first — a torn first or last line is skipped."""
    events: list[JsonObject] = []
    for line in reversed(tail.splitlines()):
        decoded = _decoded(line)
        if decoded is not None:
            events.append(decoded)
    return events


def _activity(newest: list[JsonObject]) -> ExecutionActivity:
    """The newest deciding event's activity: an outstanding tool call is ``tool``, any other output ``model``."""
    for event in newest:
        kind = event.get("type")
        if kind == "rate_limit_event":
            continue
        if kind == "tool_progress":
            return ExecutionActivity.TOOL
        if kind == "system" and event.get("subtype") in _TOOL_TASK_SUBTYPES:
            return ExecutionActivity.TOOL
        if kind == "assistant" and _requests_tool(event):
            return ExecutionActivity.TOOL
        return ExecutionActivity.MODEL
    return ExecutionActivity.UNKNOWN


def _requests_tool(event: JsonObject) -> bool:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_use" for block in content
    )
