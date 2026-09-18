"""The OpenCode export -> :class:`NormalizedTurn` normalizer (blizzard#437).

Pure and stdlib-only (``bzh:domain-core``): :func:`build_turns` takes already-parsed
:class:`~.opencode_shapes.OpenCodeMessage` objects, never a raw export string, and never touches
the exporter or a subprocess. Sidechain resolution is the transcript source's own I/O; this
module only surfaces the child-session candidates a tool part carries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from blizzard.runner.harness.internal.opencode_cursor import MessagePartIdentity
from blizzard.runner.harness.internal.opencode_shapes import OpenCodeMessage, OpenCodePart, OpenCodeSessionExport
from blizzard.runner.harness.transcript import NormalizedTurn, NormalizedTurnKind, ToolCall

#: The normalizer version stamped onto every batch; bumped when this module's output changes.
NORMALIZER_VERSION = "opencode-export/1"

#: Cap each text / thinking / tool-output string block at this many characters.
MAX_BLOCK_CHARS = 1024 * 1024


@dataclass(frozen=True)
class Text:
    """One string block, capped at :data:`MAX_BLOCK_CHARS`. Unlike Claude Code's own, OpenCode's
    export is not known to carry ANSI escapes, so nothing here strips any."""

    text: str
    truncated: bool

    @classmethod
    def of(cls, raw: str) -> Text:
        if len(raw) > MAX_BLOCK_CHARS:
            return cls(raw[:MAX_BLOCK_CHARS], True)
        return cls(raw, False)


_EMPTY = Text("", False)


@dataclass(frozen=True)
class ChildCandidate:
    """One tool part's undocumented child-session pointer, and the agent type its own input
    named. The transcript source resolves and verifies the link; this module only finds it."""

    session_id: str
    agent_type: str | None


def child_candidate_of(part: OpenCodePart) -> ChildCandidate | None:
    """``part.raw["state"]["metadata"]["sessionID"]``, read defensively — no field the strict
    parser types, confirmed only by ``contracts/opencode/1.18.25/child_session.json``. Guards
    every level; malformed or absent reads as no candidate, never raises."""
    state = part.raw.get("state")
    if not isinstance(state, Mapping):
        return None
    metadata = state.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    session_id = metadata.get("sessionID")
    if not isinstance(session_id, str) or not session_id:
        return None
    agent: str | None = None
    if part.state is not None:
        candidate_agent = part.state.input.get("agent")
        agent = candidate_agent if isinstance(candidate_agent, str) else None
    return ChildCandidate(session_id=session_id, agent_type=agent)


def harness_version_of(export: OpenCodeSessionExport) -> str | None:
    """``export.info.raw["version"]`` — carried by the export but not typed by
    :class:`~.opencode_shapes.OpenCodeSessionInfo`, whose parser does not extract it."""
    version = export.info.raw.get("version")
    return version if isinstance(version, str) and version else None


def _joined_text(message: OpenCodeMessage) -> str:
    texts = [part.text or "" for part in message.parts if part.type == "text"]
    return "\n".join(t for t in texts if t)


def build_turns(
    messages: Sequence[OpenCodeMessage], *, admitted: frozenset[MessagePartIdentity] | None
) -> tuple[list[NormalizedTurn], dict[int, ChildCandidate]]:
    """Fold ``messages``' parts into turns, in file order. ``admitted=None`` builds every part
    (a resolved child sidechain's own full conversation); otherwise only an identity in
    ``admitted`` turns into anything. A user/assistant text turn joins every *current* text
    part of its message, however many of them ``admitted`` names; a step-start/step-finish/
    compaction/snapshot/patch/agent/subtask part never produces a turn on its own."""
    turns: list[NormalizedTurn] = []
    child_candidates: dict[int, ChildCandidate] = {}
    joined_messages: set[str] = set()
    for message in messages:
        for part in message.parts:
            identity = MessagePartIdentity(message.info.id, part.id)
            if admitted is not None and identity not in admitted:
                continue
            if part.type == "text":
                if message.info.id in joined_messages:
                    continue
                joined_messages.add(message.info.id)
                kind: NormalizedTurnKind = "env" if message.info.role == "user" else "asst"
                turns.append(_text_turn(len(turns), kind, _joined_text(message)))
            elif part.type == "reasoning":
                turns.append(_thinking_turn(len(turns), part))
            elif part.type == "tool":
                index = len(turns)
                turns.append(_tool_turn(index, part))
                candidate = child_candidate_of(part)
                if candidate is not None:
                    child_candidates[index] = candidate
    return turns, child_candidates


def _text_turn(index: int, kind: NormalizedTurnKind, raw: str) -> NormalizedTurn:
    text = Text.of(raw)
    return NormalizedTurn(
        index=index,
        kind=kind,
        timestamp=None,
        text=text.text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=text.truncated,
    )


def _thinking_turn(index: int, part: OpenCodePart) -> NormalizedTurn:
    raw = part.text or ""
    text = Text.of(raw) if raw else _EMPTY
    return NormalizedTurn(
        index=index,
        kind="thinking",
        timestamp=None,
        text=text.text,
        tool=None,
        thinking_redacted=not raw,
        sidechain=None,
        truncated=text.truncated,
    )


def _tool_turn(index: int, part: OpenCodePart) -> NormalizedTurn:
    state = part.state
    assert state is not None  # OpenCodePart.parse requires `state` on every tool part
    if state.output is not None:
        raw_output: str | None = state.output
    elif state.status == "error" and state.error is not None:
        raw_output = state.error
    else:
        raw_output = None  # pending/running — a live turn, not corruption
    output_text = Text.of(raw_output) if raw_output is not None else _EMPTY
    tool = ToolCall(
        name=part.tool or "",
        input=state.input,
        input_unparsed=None,
        input_shape="object",
        tool_use_id=part.call_id,
        output=output_text.text if raw_output is not None else None,
        output_truncated=output_text.truncated,
    )
    return NormalizedTurn(
        index=index,
        kind="tool",
        timestamp=None,
        text="",
        tool=tool,
        thinking_redacted=False,
        sidechain=None,
        truncated=output_text.truncated,
    )


__all__ = [
    "MAX_BLOCK_CHARS",
    "NORMALIZER_VERSION",
    "ChildCandidate",
    "Text",
    "build_turns",
    "child_candidate_of",
    "harness_version_of",
]
