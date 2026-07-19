"""The JSONL → ``Turn`` parser (issue #29).

Pure and stdlib-only (``bzh:domain-core``): :func:`parse_turns` takes an **iterable of
strings** — already-read lines — never a path, which is what makes it unit-testable
with no filesystem (the repository adapter under ``internal/`` owns the actual file
read, including the file-size bound — see ``internal/jsonl_transcript_repository.py``).
It is the only place that knows the Claude Code JSONL record shape: an unrecognized
or malformed record is skipped silently rather than raising — a third-party format
change degrades to "fewer turns", never a crash.

Four filter rules drop records that are not conversation turns, each verified
against a real transcript and each producing visible garbage if left unfiltered:
``isMeta`` (injected non-conversational content, e.g. ``/context`` output arriving as
a fake user turn), ``isSidechain`` (a subagent's private conversation spliced inline),
the control-record ``type``s (``mode``/``permission-mode``/``last-prompt``/
``ai-title``/``queue-operation``), and ``type: "system"``/``"attachment"``. Raw ANSI
escapes in content are stripped before emission (also a real-transcript finding).

Records are read in **file order**, not DAG-traversed via ``uuid``/``parentUuid`` — a
fleet worker is ``claude -p`` (headless, ``--resume`` appends), so no rewind/branch is
ever created and traversal would be speculative complexity producing identical output.

Two caps, both applied here so they are unit-testable with no I/O: ``MAX_TURNS``
(keep the last N turns), ``MAX_BLOCK_CHARS`` (cap each text/tool_input/tool_output
block). Both favor recency — the newest turns are the ones an operator reading a live
or just-finished agent cares about. The file-size bound is a third, separate cap
enforced in the adapter, ahead of parsing — see
``internal/jsonl_transcript_repository.MAX_FILE_BYTES``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from blizzard.runner.transcripts.repository import Turn, TurnKind

#: Keep only the most recent this-many turns (post-collapse).
MAX_TURNS = 1000

#: Cap each text / tool_input / tool_output block at this many characters.
MAX_BLOCK_CHARS = 1024 * 1024

#: Control records: plumbing, never conversation.
_CONTROL_TYPES = frozenset(
    {"mode", "permission-mode", "last-prompt", "ai-title", "queue-operation", "system", "attachment"}
)

#: Raw CSI ANSI escape sequences (e.g. `\x1b[31m`) found in real transcript content.
#:
#: The full CSI grammar is matched, not just the SGR subset: `ESC [`, parameter bytes
#: `0x30-0x3F` (which includes the **private-mode `?` prefix**), intermediate bytes
#: `0x20-0x2F`, then a final byte `0x40-0x7E`. Real fleet transcripts carry
#: `\x1b[?25l` / `\x1b[?25h` (cursor hide/show, emitted by interactive TUI tools a
#: worker shells out to) alongside the more familiar SGR color codes, and both must
#: be stripped or they render as literal garbage.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class ParsedTranscript:
    """:func:`parse_turns`'s return — the collapsed turns plus the turn-count cap flag.

    ``truncated`` here reflects only the ``MAX_TURNS`` cap this function applies; the
    adapter combines it with its own file-size cap flag before it reaches
    :class:`~blizzard.runner.transcripts.repository.Transcript.truncated`.
    """

    turns: list[Turn]
    truncated: bool


def parse_turns(lines: list[str]) -> ParsedTranscript:
    """Collapse raw JSONL lines into the panel's turn vocabulary.

    | Raw record | → Turn |
    |---|---|
    | non-meta, non-sidechain `user` (no `tool_result` blocks) | one `env` |
    | `assistant`, text blocks | one `asst` (blocks joined) |
    | `assistant`, each `tool_use` block | one `tool` each — output pending |
    | `user` carrying `tool_result` blocks | not a turn — matched by `tool_use_id` |

    An unmatched `tool_result` is dropped; a `tool` turn whose result never arrives
    keeps `tool_output=None` (renders "running…" — the correct live state, not
    corruption — while the file is still being appended to by a live process).
    """
    pending_tool_index: dict[str, int] = {}
    turns: list[dict[str, Any]] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # A truncated final line (the file is live-appended-to) or any other
            # malformed record — dropped silently, not an error.
            continue
        if not isinstance(record, dict):
            continue
        if _skip_record(record):
            continue

        record_type = record.get("type")
        timestamp = _parse_timestamp(record.get("timestamp"))

        if record_type == "assistant":
            _collapse_assistant(record, timestamp, turns, pending_tool_index)
        elif record_type == "user":
            _collapse_user(record, timestamp, turns, pending_tool_index)

    turns_truncated = len(turns) > MAX_TURNS
    kept = turns[-MAX_TURNS:] if turns_truncated else turns

    built = [
        Turn(
            index=i,
            kind=t["kind"],
            timestamp=t["timestamp"],
            text=t["text"],
            tool_name=t["tool_name"],
            tool_input=t["tool_input"],
            tool_output=t["tool_output"],
            truncated=t["truncated"],
        )
        for i, t in enumerate(kept)
    ]

    return ParsedTranscript(turns=built, truncated=turns_truncated)


# --- record → turn collapse -------------------------------------------------


def _skip_record(record: dict[str, Any]) -> bool:
    if record.get("isMeta"):
        return True
    if record.get("isSidechain"):
        return True
    return record.get("type") in _CONTROL_TYPES


def _collapse_assistant(
    record: dict[str, Any],
    timestamp: datetime | None,
    turns: list[dict[str, Any]],
    pending_tool_index: dict[str, int],
) -> None:
    content = record.get("message", {}).get("content") if isinstance(record.get("message"), dict) else None
    if not isinstance(content, list):
        return

    text_parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
    joined = "\n".join(p for p in text_parts if p)
    if joined:
        text, block_truncated = _clean(joined)
        turns.append(_new_turn("asst", timestamp, text=text, truncated=block_truncated))

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name", ""))
        raw_input = block.get("input")
        input_text, input_truncated = _clean(json.dumps(raw_input) if raw_input is not None else "")
        turns.append(_new_turn("tool", timestamp, tool_name=name, tool_input=input_text, truncated=input_truncated))
        tool_use_id = block.get("id")
        if isinstance(tool_use_id, str):
            pending_tool_index[tool_use_id] = len(turns) - 1


def _collapse_user(
    record: dict[str, Any],
    timestamp: datetime | None,
    turns: list[dict[str, Any]],
    pending_tool_index: dict[str, int],
) -> None:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None

    tool_result_blocks = None
    if isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        tool_result_blocks = blocks or None

    if tool_result_blocks is not None:
        for block in tool_result_blocks:
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str):
                continue
            index = pending_tool_index.get(tool_use_id)
            if index is None:
                continue  # unmatched tool_result — dropped
            output, output_truncated = _clean(_extract_text(block.get("content")))
            turns[index]["tool_output"] = output
            turns[index]["truncated"] = turns[index]["truncated"] or output_truncated
        return

    # A plain user record (first spawn prompt, or a later --resume injection) — env.
    text, block_truncated = _clean(_extract_text(content))
    turns.append(_new_turn("env", timestamp, text=text, truncated=block_truncated))


def _new_turn(
    kind: TurnKind,
    timestamp: datetime | None,
    *,
    text: str = "",
    tool_name: str | None = None,
    tool_input: str | None = None,
    tool_output: str | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "timestamp": timestamp,
        "text": text,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "truncated": truncated,
    }


# --- plumbing ----------------------------------------------------------------


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean(text: str) -> tuple[str, bool]:
    """Strip ANSI escapes, then cap at :data:`MAX_BLOCK_CHARS` — returns ``(text, truncated)``."""
    stripped = _ANSI_RE.sub("", text)
    if len(stripped) > MAX_BLOCK_CHARS:
        return stripped[:MAX_BLOCK_CHARS], True
    return stripped, False
