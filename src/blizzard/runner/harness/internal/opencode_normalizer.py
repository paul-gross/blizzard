"""The OpenCode export -> :class:`NormalizedTurn` normalizer (blizzard#437).

Pure and stdlib-only (``bzh:domain-core``): :func:`build_turns` takes already-parsed
:class:`~.opencode_shapes.OpenCodeMessage` objects, never a raw export string, and never touches
the exporter or a subprocess. Sidechain resolution is the transcript source's own I/O; this
module only surfaces the child-session candidates a tool part carries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from blizzard.runner.harness.internal.opencode_cursor import MessagePartIdentity
from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodeMessage,
    OpenCodePart,
    OpenCodeSessionExport,
    OpenCodeToolState,
)
from blizzard.runner.harness.transcript import LateToolOutput, NormalizedTurn, NormalizedTurnKind, ToolCall

#: The normalizer version stamped onto every batch; bumped when this module's output changes.
NORMALIZER_VERSION = "opencode-export/1"

#: Cap each text / thinking / tool-output string block at this many characters.
MAX_BLOCK_CHARS = 1024 * 1024


@dataclass(frozen=True)
class Text:
    """One string block, capped at :data:`MAX_BLOCK_CHARS`. OpenCode's own export carries
    plain text — no ANSI escape sequences observed in any pinned fixture — so this block
    strips none; it only truncates."""

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
) -> tuple[list[NormalizedTurn], dict[MessagePartIdentity, int]]:
    """Fold ``messages``' parts into turns, in file order. ``admitted=None`` builds every part
    (a resolved child sidechain's own full conversation); otherwise only an identity in
    ``admitted`` turns into anything; a step-start/step-finish/compaction/snapshot/patch/agent/
    subtask part never produces one. The second return names every built tool turn's own
    identity by index — the source's own hook for attaching a resolved sidechain onto it."""
    turns: list[NormalizedTurn] = []
    tool_turns: dict[MessagePartIdentity, int] = {}
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
                tool_turns[identity] = index
    return turns, tool_turns


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


def _tool_output_text(state: OpenCodeToolState) -> str | None:
    """The tool state's own output text: ``state.output`` when present, else ``state.error``
    on an ``"error"`` status. ``None`` while pending/running — a live turn, not corruption."""
    if state.output is not None:
        return state.output
    if state.status == "error" and state.error is not None:
        return state.error
    return None


def late_tool_output_of(part: OpenCodePart) -> LateToolOutput | None:
    """The output patch for a tool part the cursor admits as an ``"updated"`` revision to an
    identity already shipped — the pending/running to completed/error transition the spec's
    "a later completed state produces the output patch" names. ``None`` when the revision
    still carries no output (e.g. pending to running): nothing yet to patch."""
    state = part.state
    assert state is not None  # OpenCodePart.parse requires `state` on every tool part
    raw_output = _tool_output_text(state)
    if raw_output is None or part.call_id is None:
        return None
    text = Text.of(raw_output)
    return LateToolOutput(tool_use_id=part.call_id, output=text.text, output_truncated=text.truncated)


def _tool_turn(index: int, part: OpenCodePart) -> NormalizedTurn:
    state = part.state
    assert state is not None  # OpenCodePart.parse requires `state` on every tool part
    raw_output = _tool_output_text(state)
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
    "late_tool_output_of",
]
