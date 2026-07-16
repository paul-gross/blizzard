"""JSONL transcript record fixtures (issue #29).

Mints individual record lines shaped like a real Claude Code session, reusable
across the parser and repository tests. ``mock-claude-code`` mints a real
Claude-shaped transcript for every fleet run — proven end-to-end at the service
tier (``tests/service/test_runner_service.py::
test_transcript_is_read_back_through_the_runner_http_api``), which is the guard
against this unit tier quietly closing the loop on itself.

These fixtures still hand-author lines at the unit tier because they cover shapes
the mock deliberately never mints — ``meta_record``, ``sidechain_record``,
``control_record``, ``ansi_private_mode_text``, ``truncated_line`` — the edge
cases a real fleet run doesn't exercise on demand. The seam design makes that
hermetic: :func:`parse_turns` takes an iterable of strings, and
:class:`~blizzard.runner.transcripts.internal.jsonl_transcript_repository
.JsonlTranscriptRepository` takes ``projects_root`` as a constructor arg, so a
test writes these lines under ``tmp_path`` directly — no ``HOME`` monkey-patching.
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
    tool_use_id: str, name: str, tool_input: dict[str, Any], *, ts: str = "2026-07-16T10:00:02Z"
) -> str:
    """An assistant record with one `tool_use` block (collapses to `tool`, output pending)."""
    content = [{"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}]
    return _line(
        {"type": "assistant", "message": {"role": "assistant", "content": content}, "timestamp": ts, "uuid": "a2"}
    )


def tool_result(tool_use_id: str, content: str, *, ts: str = "2026-07-16T10:00:03Z") -> str:
    """A `tool_result` carrier — not a turn, matched by `tool_use_id`."""
    blocks = [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}]
    return _line(
        {
            "type": "user",
            "message": {"role": "user", "content": blocks},
            "toolUseResult": {},
            "timestamp": ts,
            "uuid": "u2",
        }
    )


def meta_record(text: str = "/context output") -> str:
    """An `isMeta` record — injected non-conversational content, filtered."""
    return _line({"type": "user", "message": {"role": "user", "content": text}, "isMeta": True, "uuid": "m1"})


def sidechain_record(text: str = "subagent chatter") -> str:
    """An `isSidechain` record — a subagent's spliced-in conversation, filtered."""
    return _line(
        {"type": "assistant", "message": {"role": "assistant", "content": text}, "isSidechain": True, "uuid": "s1"}
    )


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
