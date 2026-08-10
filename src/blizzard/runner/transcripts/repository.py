"""Transcript domain types and the read-only repository seam (issue #29, widened blizzard#248).

:class:`Turn`/:class:`Transcript` are the parsed read model, carrying the hub segment wire's own turn
shape — thinking turns and sidechains included (blizzard#248 D1/D2). A missing or unreadable transcript
is a **normal** outcome, not an exception: ``.available``/``.reason`` carry it in-band. Read-only by
design (``bzh:repository-split``) — the separate outbound lane (issue #246) does the writing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

#: The shared turn wire vocabulary — closes ``TurnSegmentView.kind`` too (blizzard#248 D1),
#: widened for thinking/unlinked-sidechain (D2); ``ask``/``verdict`` stay deferred, not derivable here.
TurnKind = Literal["env", "asst", "tool", "thinking", "sidechain"]

#: Why a transcript is unavailable — all three are ordinary, expected states of a
#: healthy agent, never a fault on their own; only ``unreadable`` logs at ERROR.
TranscriptUnavailable = Literal["spawning", "not_found", "unreadable"]

#: Which side answered a resolved transcript (D1) — the wire's ``provenance`` field.
TranscriptProvenance = Literal["local", "archived"]


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation, structured — mirrors
    :class:`~blizzard.runner.harness.transcript.ToolCall`. Carried through, never
    re-materialized to a JSON string (blizzard#248 D1) — rendering structured ``input``
    is the viewer's job."""

    name: str
    input: Mapping[str, object]
    input_unparsed: str | None
    input_shape: str
    tool_use_id: str | None
    output: str | None
    output_truncated: bool


@dataclass(frozen=True)
class Sidechain:
    """A subagent's private conversation, nested under its spawning tool turn (or, when
    ``link == "unlinked"``, carried as its own top-level ``"sidechain"`` turn instead) —
    mirrors :class:`~blizzard.runner.harness.transcript.SidechainConversation`. Recursive:
    one of ``turns`` may itself carry a tool call whose own sidechain nests further."""

    agent_id: str | None
    agent_type: str | None
    link: str
    turns: list[Turn]


@dataclass(frozen=True)
class Turn:
    """One conversation turn, carried in full (blizzard#248 D2). ``tool``/``sidechain`` populate only
    on a ``kind="tool"`` turn, except a ``"sidechain"`` turn's own ``sidechain``, which stands alone
    (unlinked); ``thinking_redacted`` is ``kind="thinking"``-only. ``tool.output`` is ``None`` while
    pending; ``truncated`` is block-level, distinct from :attr:`Transcript.truncated`."""

    index: int
    kind: TurnKind
    timestamp: datetime | None
    text: str
    tool: ToolCall | None
    thinking_redacted: bool
    sidechain: Sidechain | None
    truncated: bool


@dataclass(frozen=True)
class Transcript:
    """A lease's parsed session — the transcript read model. ``available=False`` carries
    ``reason`` and an empty ``turns``, so a caller must check it before reading ``turns``.
    ``truncated`` is file-level: the tail-byte cap, ``MAX_TURNS``, or a sidechain-only read
    budget cut content the panel renders, distinct from a turn's own :attr:`Turn.truncated`."""

    session_id: str | None
    available: bool
    reason: TranscriptUnavailable | None
    turns: list[Turn]
    truncated: bool


class IReadTranscriptRepository(Protocol):
    """The transcript lookup seam. Read-only (``bzh:repository-split``).

    One operation. Raw-lines and size-on-disk reads are a separate concern, reached off
    the harness transcript source directly rather than through this seam."""

    def read_turns(self, session_id: str, *, spawn_cwd: str | None) -> Transcript:
        """The session's parsed transcript, located by ``session_id`` alone.

        ``spawn_cwd`` is an optional **disambiguation hint**, not the lookup key — used
        only when more than one project directory holds a file with this session id, and
        legitimately ``None``."""
        ...
