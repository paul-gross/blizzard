"""JSONL transcript record fixtures (issue #29, extended blizzard#245).

Mints individual record lines shaped like a real Claude Code session, reusable
across the parser/normalizer and repository/source tests. ``mock-claude-code`` mints
a real Claude-shaped transcript for every fleet run — proven end-to-end at the service
tier (``tests/service/test_runner_service.py::
test_transcript_is_read_back_through_the_runner_http_api``), which is the guard
against this unit tier quietly closing the loop on itself.

These fixtures still hand-author lines at the unit tier because they cover shapes
the mock deliberately never mints — ``meta_record``, ``sidechain_record``,
``control_record``, ``ansi_private_mode_text``, ``truncated_line``, and (blizzard#245)
``thinking_block``, ``sidecar_record``, ``sidechain_run_record``, ``versioned``. Why the
mock never mints the blizzard#245 shapes is stated once, in
``blizzard-mock/src/blizzard_mock/harness/README.md`` ("Conversation transcripts") —
not restated here. The seam design makes hand-authoring hermetic:
:func:`~blizzard.runner.harness.internal.claude_code_normalizer.normalize_lines`
takes an iterable of strings, and the repository/source adapters take
``projects_root`` as a constructor arg, so a test writes these lines under
``tmp_path`` directly — no ``HOME`` monkey-patching.
"""

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
    """A `tool_result` carrier — not a turn, matched by `tool_use_id`.

    ``agent_id`` stamps `toolUseResult.agentId` — the exact
    join key the spawning `Agent`/`Task` call's result carries, matching a sidecar
    file's own `agentId` (link route 1's primary, corpus-measured path).
    """
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
    """An `isSidechain` record — a subagent's spliced-in conversation.

    The normalizer routes this to inline-sidechain assembly (nested on its spawning
    tool call, or `unlinked_sidechains` when no route resolves one) rather than
    dropping it — the panel projection is what filters sidechains back out, not the
    normalizer this fixture most often feeds.
    """
    return _line(
        {"type": "assistant", "message": {"role": "assistant", "content": text}, "isSidechain": True, "uuid": "s1"}
    )


def thinking_block(
    *, text: str = "", signature: str | None = "sig-1", ts: str = "2026-07-16T10:00:00Z", uuid: str = "t1"
) -> str:
    """An assistant record carrying one `thinking` content block.

    Redacted by default (all 7,812 sampled thinking blocks in a real corpus
    carried empty `thinking` plus a `signature`) — the expected shape, not an edge
    case. Pass ``text`` non-empty and ``signature=None`` for the non-redacted case.
    """
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
    """One record of a sidecar file (the corpus-primary sidechain shape):
    `<project-dir>/<session-id>/subagents/agent-<agentId>.jsonl`. Every
    record in a real sidecar file carries `isSidechain: true`, `sessionId` (the
    parent session), and `agentId` — read whole (all its own lines are one
    subagent's conversation), never mixed with a top-level session file's lines.
    """
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
    """One record of an **inline** sidechain run (the issue's originally-described
    layout — corpus-unobserved, kept as the fallback link routes 2/3 exist for).
    `isSidechain: true` plus a `uuid`/`parentUuid` thread: the run's
    root record's `parent_uuid` names the spawning tool call's assistant record
    (route 2's join key); each later record in the run names the previous one.
    """
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
    """An assistant text block carrying **private-mode** CSI escapes, stripped on parse.

    Deliberately distinct from :func:`ansi_text`'s SGR (`\\x1b[31m`): taken verbatim
    from real fleet transcripts, `\\x1b[?25l` / `\\x1b[?25h` (cursor hide/show, emitted
    by interactive TUI tools a worker shells out to) carry the `?` private-mode
    parameter prefix, so this fixture exercises that prefix specifically rather than
    only the SGR subset :func:`ansi_text` covers.
    """
    return assistant_text(f"\x1b[?25l{visible}\x1b[?25h")


def truncated_line() -> str:
    """A partial line — the steady state while a live process appends to the file."""
    return '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "te'


def _line(record: dict[str, Any]) -> str:
    return json.dumps(record)
