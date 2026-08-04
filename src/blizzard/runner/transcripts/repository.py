"""Transcript domain types and the read-only repository seam (issue #29).

:class:`Turn` and :class:`Transcript` are the parsed read model — a lease's session
JSONL collapsed into the panel's turn vocabulary (``env``/``asst``/``tool``). A missing
or unreadable transcript is a **normal** outcome, not an exception:
``Transcript.available`` and ``.reason`` carry it in-band so the API route never
needs a 5xx for "the agent hasn't written anything yet" or "the file was cleaned up".

:class:`IReadTranscriptRepository` is the inner seam (``bzh:dependency-inversion``):
this module declares it, :mod:`.internal.projected_transcript_repository` implements
it as a projection over the harness's own transcript source (blizzard#245). Read-only
by design (``bzh:repository-split``): nothing in blizzard writes a transcript, so
there is no ``IWrite…`` variant.

Error logging on an unreadable transcript is now the harness seam's own concern
(:class:`~blizzard.runner.harness.transcript.TranscriptErrorFactory`) — a transcript
that exists but cannot be read is still a normal (if degraded) read,
``available=False, reason="unreadable"``, exactly as before; this module just no
longer owns the factory that logs it, since it no longer touches the filesystem.
"""

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


@dataclass(frozen=True)
class Turn:
    """One collapsed conversation turn.

    ``tool_output`` is ``None`` while a ``tool`` turn's result has not yet arrived in
    the file (the live steady state — renders as "running…", not corruption).
    ``truncated`` is block-level: ``text``/``tool_input``/``tool_output`` were each
    capped at ``MAX_BLOCK_CHARS`` and this turn lost content to that cap (distinct
    from :attr:`Transcript.truncated`, which is file/turn-count-level).
    """

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
    """A lease's parsed session — the transcript route's domain read model.

    ``available=False`` carries ``reason`` and an empty ``turns``; a caller
    must check ``available`` before reading ``turns``. ``truncated`` is file-level:
    the tail-byte cap or ``MAX_TURNS`` dropped some of the oldest turns —
    distinct from a turn's own :attr:`Turn.truncated`.
    """

    session_id: str | None
    available: bool
    reason: TranscriptUnavailable | None
    turns: list[Turn]
    truncated: bool


class IReadTranscriptRepository(Protocol):
    """The transcript lookup seam. Read-only (``bzh:repository-split``)."""

    def read_turns(self, session_id: str, *, spawn_cwd: str | None) -> Transcript:
        """The session's parsed transcript, located by ``session_id`` alone.

        ``spawn_cwd`` is an optional **disambiguation hint** — not the lookup
        key — used only when more than one project directory holds a file with this
        session id; it is legitimately ``None`` for every closed lease
        (:class:`~blizzard.runner.domain.leases.LeaseActivity` owns the
        closed-binding-release invariant), and the glob-by-session-id primary path
        does not need it at all.
        """
        ...

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        """The session's raw transcript lines, unparsed — empty when none exist or the
        file is unreadable (issue #58's envelope-less usage fallback).

        Same location rule as :meth:`read_turns` (session-id glob, ``spawn_cwd`` an
        optional disambiguation hint only); this sibling skips normalization entirely —
        the caller (``sum_transcript_usage``) wants the raw per-message ``usage``
        objects, not the panel's collapsed turns.
        """
        ...

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """The session transcript's size on disk, or ``None`` when it cannot be read.

        The signal behind a declared ``rotate.max_transcript_bytes`` (issue #144). Same
        location rule as the two reads above.

        ``None`` is an **unknown**, never a zero: no file exists yet (the session was just
        minted), or the file could not be stat'd. The rotation check treats an unreadable
        signal as "not measured" and lets the head stand — a missing measurement is not a
        breach — so returning 0 here would silently read as "well under bound" and make
        the threshold inert in exactly the case it cannot see.

        Reads the size, never the content: an unbounded transcript is precisely what this
        threshold exists to catch, so it must not be read into memory to measure.
        """
        ...
