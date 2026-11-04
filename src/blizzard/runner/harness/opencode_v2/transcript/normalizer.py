"""OpenCode 2 messages into :class:`NormalizedTurn`\\ s, stamped ``opencode-v2/1``. Pure: it reads parsed
:class:`~.opencode_v2.shapes.Message`\\ s and never the service. A step still being written ships its parts only up to
its last call, so text that may still grow is held back, and a shipped call's output follows as a late patch."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.opencode_v2.shapes import Message, Part
from blizzard.runner.harness.transcript import LateToolOutput, NormalizedTurn, NormalizedTurnKind, ToolCall

NORMALIZER_VERSION = "opencode-v2/1"
#: Each text, thinking and tool-output block is capped at this many characters.
MAX_BLOCK_CHARS = 1024 * 1024
#: The call that spawns a child session.
SUBAGENT_TOOL = "subagent"

_OPEN_TOOL_STATES = frozenset({"pending", "streaming", "running"})
#: Messages that hand the model text it did not write.
_ENV_TYPES = frozenset({"user", "synthetic"})


@domain_model
@dataclass(frozen=True)
class MessageTurns:
    """The turns one message's parts made, each call's turn by its call id, and the calls that shipped unfinished."""

    turns: list[NormalizedTurn] = field(default_factory=list[NormalizedTurn])
    calls: dict[str, int] = field(default_factory=dict[str, int])
    pending: tuple[str, ...] = ()


def _capped(raw: str) -> tuple[str, bool]:
    return (raw[:MAX_BLOCK_CHARS], True) if len(raw) > MAX_BLOCK_CHARS else (raw, False)


def _utc(epoch_ms: int | None) -> datetime | None:
    if epoch_ms is None:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _open_call(part: Part) -> bool:
    return part.type == "tool" and part.state is not None and part.state.status in _OPEN_TOOL_STATES


def is_open(message: Message) -> bool:
    """A step still being written: not yet completed, or still running a call."""
    return message.type == "assistant" and (message.completed is None or any(map(_open_call, message.parts)))


def shippable(message: Message) -> int:
    """How many of ``message``'s parts can ship now: all of a settled message's, an open one's through its last call."""
    if not is_open(message):
        return len(message.parts)
    calls = [index for index, part in enumerate(message.parts) if part.type == "tool"]
    return calls[-1] + 1 if calls else 0


def tool_output(part: Part) -> str | None:
    """A finished call's output — its error's message when it failed; ``None`` while it runs."""
    state = part.state
    if state is None or state.status in _OPEN_TOOL_STATES:
        return None
    if state.status == "error":
        return state.error or ""
    return state.output if state.output is not None else ""


def spawned_children(message: Message) -> dict[str, str]:
    """Each child session a ``subagent`` call in ``message`` spawned, to the call's id."""
    return {
        part.state.child_session_id: part.id
        for part in message.parts
        if part.name == SUBAGENT_TOOL and part.id and part.state is not None and part.state.child_session_id
    }


def message_turns(message: Message, start: int = 0) -> MessageTurns:
    """The turns ``message`` makes from its part ``start`` on, as far as it can ship now."""
    created = _utc(message.created)
    if message.type in _ENV_TYPES or message.type == "compaction":
        if start or not message.text:
            return MessageTurns()
        return MessageTurns([_text_turn(0, "env", message.text, created)])
    if message.type != "assistant":
        return MessageTurns()
    result = MessageTurns()
    pending: list[str] = []
    for part in message.parts[start : shippable(message)]:
        timestamp = _utc(part.created) or created
        index = len(result.turns)
        if part.type == "text" and part.text:
            result.turns.append(_text_turn(index, "asst", part.text, timestamp))
        elif part.type == "reasoning":
            result.turns.append(_thinking_turn(index, part.text, timestamp))
        elif part.type == "tool":
            result.turns.append(_tool_turn(index, part, timestamp))
            if part.id:
                result.calls[part.id] = index
                if tool_output(part) is None:
                    pending.append(part.id)
    return MessageTurns(result.turns, result.calls, tuple(pending))


def late_outputs(message: Message, pending: Sequence[str]) -> tuple[list[LateToolOutput], tuple[str, ...]]:
    """The output of each ``pending`` call that has now finished, and the calls still waiting."""
    parts: Mapping[str, Part] = {part.id: part for part in message.parts if part.type == "tool" and part.id}
    patches: list[LateToolOutput] = []
    waiting: list[str] = []
    for call_id in pending:
        part = parts.get(call_id)
        output = None if part is None else tool_output(part)
        if output is None:
            waiting.append(call_id)
            continue
        text, truncated = _capped(output)
        patches.append(LateToolOutput(tool_use_id=call_id, output=text, output_truncated=truncated))
    return patches, tuple(waiting)


def _text_turn(index: int, kind: NormalizedTurnKind, raw: str, timestamp: datetime | None) -> NormalizedTurn:
    text, truncated = _capped(raw)
    return NormalizedTurn(
        index=index,
        kind=kind,
        timestamp=timestamp,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=truncated,
    )


def _thinking_turn(index: int, raw: str, timestamp: datetime | None) -> NormalizedTurn:
    text, truncated = _capped(raw)
    return NormalizedTurn(
        index=index,
        kind="thinking",
        timestamp=timestamp,
        text=text,
        tool=None,
        thinking_redacted=not raw,
        sidechain=None,
        truncated=truncated,
    )


def _tool_turn(index: int, part: Part, timestamp: datetime | None) -> NormalizedTurn:
    raw = tool_output(part)
    output, truncated = _capped(raw) if raw is not None else (None, False)
    tool = ToolCall(
        name=part.name or "",
        input=part.state.input if part.state is not None else {},
        input_unparsed=None,
        input_shape="object" if part.state is not None else "absent",
        tool_use_id=part.id,
        output=output,
        output_truncated=truncated,
    )
    return NormalizedTurn(
        index=index,
        kind="tool",
        timestamp=timestamp,
        text="",
        tool=tool,
        thinking_redacted=False,
        sidechain=None,
        truncated=truncated,
    )


__all__ = [
    "MAX_BLOCK_CHARS",
    "NORMALIZER_VERSION",
    "SUBAGENT_TOOL",
    "MessageTurns",
    "is_open",
    "late_outputs",
    "message_turns",
    "shippable",
    "spawned_children",
    "tool_output",
]
