"""Transcript domain types and the read-only repository seam (issue #29).

:class:`Turn` and :class:`Transcript` are the parsed read model. A missing or unreadable
transcript is a **normal** outcome, not an exception: ``Transcript.available`` and
``.reason`` carry it in-band. Read-only by design (``bzh:repository-split``) — nothing in
blizzard writes a transcript, so there is no ``IWrite…`` variant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

#: The panel's turn vocabulary. ``ask``/``verdict`` are deferred —
#: not derivable from raw records alone (they need facts this package never reads).
TurnKind = Literal["env", "asst", "tool"]

#: Why a transcript is unavailable — all three are ordinary, expected states of a
#: healthy agent, never a fault on their own; only ``unreadable`` logs at ERROR.
TranscriptUnavailable = Literal["spawning", "not_found", "unreadable"]

#: Which side answered a resolved transcript (D1) — the wire's ``provenance`` field,
#: co-located with its siblings above rather than in ``transcripts/service.py``.
TranscriptProvenance = Literal["local", "archived"]

#: Keep only the most recent this-many turns (post-projection) — bounds the panel
#: payload to the newest, most relevant conversation on a long-running session. Shared by
#: both the local-file and hub-archived projections, so it lives beside the read model
#: both depend on rather than in either transport's own ``internal/`` module.
MAX_TURNS = 1000

#: Cap a tool call's *serialized* input at this many characters — shared by both
#: projections; a re-materialization-time cap, distinct from the harness normalizer's own
#: text-block cap (``claude_code_normalizer.MAX_BLOCK_CHARS``).
MAX_BLOCK_CHARS = 1024 * 1024


@dataclass(frozen=True)
class Turn:
    """One collapsed conversation turn. ``tool_output`` is ``None`` while a ``tool``
    turn's result has not yet arrived — the live steady state, not corruption.
    ``truncated`` is block-level: this turn lost content to ``MAX_BLOCK_CHARS``, distinct
    from :attr:`Transcript.truncated`, which is file/turn-count-level."""

    index: int
    kind: TurnKind
    timestamp: datetime | None
    text: str
    tool_name: str | None
    tool_input: str | None
    tool_output: str | None
    truncated: bool


@dataclass(frozen=True)
class Transcript:
    """A lease's parsed session — the transcript read model. ``available=False`` carries
    ``reason`` and an empty ``turns``, so a caller must check it before reading ``turns``.
    ``truncated`` is file-level: the tail-byte cap or ``MAX_TURNS`` dropped the oldest
    turns, distinct from a turn's own :attr:`Turn.truncated`."""

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
