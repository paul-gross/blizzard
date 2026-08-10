"""JSONL transcript record fixtures (issue #29, extended blizzard#245).

Mints individual record lines shaped like a real Claude Code session, reusable across
the parser/normalizer and repository/source tests. These fixtures hand-author shapes
the mock deliberately never mints; why is stated once, in
``blizzard-mock/src/blizzard_mock/harness/README.md`` ("Conversation transcripts")."""

from __future__ import annotations

import json
from typing import Any


def user_env(text: str, *, ts: str = "2026-07-16T10:00:00Z", uuid: str = "u1") -> str:
    """A plain user record — the spawn prompt or a `--resume` injection (collapses to `env`)."""
    return _line({"type": "user", "message": {"role": "user", "content": text}, "timestamp": ts, "uuid": uuid})


def assistant_text(text: str, *, ts: str = "2026-07-16T10:00:01Z", uuid: str = "a1") -> str:
    """An assistant record with a single text block (collapses to `asst`)."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    return _line(
        {"type": "assistant", "message": {"role": "assistant", "content": content}, "timestamp": ts, "uuid": uuid}
    )


def assistant_tool_use(
    tool_use_id: str,
    name: str,
    tool_input: dict[str, Any],
    *,
    ts: str = "2026-07-16T10:00:02Z",
    uuid: str = "a2",
) -> str:
    """An assistant record with one `tool_use` block (collapses to `tool`, output pending)."""
    content = [{"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}]
    return _line(
        {"type": "assistant", "message": {"role": "assistant", "content": content}, "timestamp": ts, "uuid": uuid}
    )


def tool_result(
    tool_use_id: str, content: str, *, ts: str = "2026-07-16T10:00:03Z", agent_id: str | None = None
) -> str:
    """A `tool_result` carrier — not a turn, matched by `tool_use_id`. ``agent_id``
    stamps `toolUseResult.agentId`, matching a sidecar file's own `agentId`."""
    blocks = [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}]
    tool_use_result: dict[str, Any] = {"agentId": agent_id} if agent_id else {}
    return _line(
        {
            "type": "user",
            "message": {"role": "user", "content": blocks},
            "toolUseResult": tool_use_result,
            "timestamp": ts,
            "uuid": "u2",
        }
    )


def meta_record(text: str = "/context output") -> str:
    """An `isMeta` record — injected non-conversational content, filtered."""
    return _line({"type": "user", "message": {"role": "user", "content": text}, "isMeta": True, "uuid": "m1"})


def sidechain_record(text: str = "subagent chatter") -> str:
    """An `isSidechain` record with no resolvable `parentUuid` chain, so the normalizer
    routes it to `unlinked_sidechains` — carried through as its own top-level
    `"sidechain"` turn by the projection (blizzard#248 D2/D7)."""
    content = [{"type": "text", "text": text}]
    return _line(
        {"type": "assistant", "message": {"role": "assistant", "content": content}, "isSidechain": True, "uuid": "s1"}
    )


def thinking_block(
    *, text: str = "", signature: str | None = "sig-1", ts: str = "2026-07-16T10:00:00Z", uuid: str = "t1"
) -> str:
    """An assistant record carrying one `thinking` content block. Redacted by default —
    the corpus-normal shape; pass ``text`` non-empty and ``signature=None`` otherwise."""
    content = [{"type": "thinking", "thinking": text, "signature": signature}]
    return _line(
        {"type": "assistant", "message": {"role": "assistant", "content": content}, "timestamp": ts, "uuid": uuid}
    )


def sidecar_record(
    text: str,
    *,
    role: str = "assistant",
    session_id: str = "parent-session",
    agent_id: str = "agent-1",
    ts: str = "2026-07-16T10:05:00Z",
    uuid: str = "sc1",
) -> str:
    """One record of a sidecar file (the corpus-primary sidechain shape). Every real
    sidecar record carries `isSidechain: true`, `sessionId`, and `agentId`."""
    content: Any = [{"type": "text", "text": text}] if role == "assistant" else text
    return _line(
        {
            "type": role,
            "message": {"role": role, "content": content},
            "isSidechain": True,
            "sessionId": session_id,
            "agentId": agent_id,
            "timestamp": ts,
            "uuid": uuid,
        }
    )


def sidechain_run_record(
    text: str,
    *,
    uuid: str,
    parent_uuid: str,
    role: str = "assistant",
    ts: str = "2026-07-16T10:05:00Z",
) -> str:
    """One record of an inline sidechain run: `isSidechain: true` plus a
    `uuid`/`parentUuid` thread, the root's `parent_uuid` naming the spawning tool
    call's assistant record."""
    content: Any = [{"type": "text", "text": text}] if role == "assistant" else text
    return _line(
        {
            "type": role,
            "message": {"role": role, "content": content},
            "isSidechain": True,
            "uuid": uuid,
            "parentUuid": parent_uuid,
            "timestamp": ts,
        }
    )


def versioned(line: str, version: str = "2.1.220") -> str:
    """Stamp an existing fixture line with a harness `version` field: every real
    conversation record carries one — the harness-version stamp is read
    off records, never a `claude --version` subprocess."""
    record = json.loads(line)
    record["version"] = version
    return _line(record)


def control_record(record_type: str = "permission-mode") -> str:
    """A control/plumbing record — no `timestamp`/`uuid` in the real thing. Filtered."""
    return _line({"type": record_type})


def ansi_text(visible: str) -> str:
    """An assistant text block carrying raw SGR ANSI escapes, stripped on parse."""
    return assistant_text(f"\x1b[31m{visible}\x1b[0m")


def ansi_private_mode_text(visible: str) -> str:
    """An assistant text block carrying **private-mode** CSI escapes, stripped on parse
    — `\\x1b[?25l`/`\\x1b[?25h`, distinct from :func:`ansi_text`'s SGR subset."""
    return assistant_text(f"\x1b[?25l{visible}\x1b[?25h")


def truncated_line() -> str:
    """A partial line — the steady state while a live process appends to the file."""
    return '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "te'


def _line(record: dict[str, Any]) -> str:
    return json.dumps(record)
