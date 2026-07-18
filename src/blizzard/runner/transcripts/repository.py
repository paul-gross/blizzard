"""Transcript domain types and the read-only repository seam (issue #29).

:class:`Turn` and :class:`Transcript` are the parsed read model — a lease's session
JSONL collapsed into the panel's turn vocabulary (``env``/``asst``/``tool``). A missing
or unreadable transcript is a **normal** outcome, not an exception:
``Transcript.available`` and ``.reason`` carry it in-band so the API route never
needs a 5xx for "the agent hasn't written anything yet" or "the file was cleaned up".

:class:`IReadTranscriptRepository` is the inner seam (``bzh:dependency-inversion``):
this module declares it, :mod:`.internal.jsonl_transcript_repository` implements it as
the package's filesystem adapter. Read-only by design (``bzh:repository-split``):
nothing in blizzard writes a transcript, so there is no ``IWrite…`` variant.

Errors are logged by an injected :class:`TranscriptErrorFactory`
(the exemplar's ``RepoErrorFactory`` shape, ``../exemplars/python/repo_pattern.py``,
narrowed here). Unlike the exemplar, there is no error type to propagate: a
transcript that exists but cannot be read is still a normal (if degraded) read —
``available=False, reason="unreadable"`` — nothing ever raises or catches past this
module's boundary, so the factory's one job is the single ERROR log site
``bzh:structlog-logging`` mandates for a wrapped I/O fault (``standards/logging.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import structlog

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


class TranscriptErrorFactory:
    """The injected error-logging seam for transcript I/O (narrowed from the
    exemplar's ``RepoErrorFactory`` shape, ``../exemplars/python/repo_pattern.py``).

    One ``from_<transport>`` method per underlying exception type it knows how to
    translate, called at the boundary where the library exception is caught. Logs
    once (structlog, ERROR) — the single log site ``bzh:structlog-logging`` mandates
    for a wrapped I/O fault. There is no error type to construct or return: the one
    caller (:class:`.internal.jsonl_transcript_repository.JsonlTranscriptRepository`)
    never re-raises or re-logs — it degrades straight to
    ``Transcript(available=False, reason="unreadable")`` (no 5xx for a filesystem
    fault the operator can do nothing about from here), so the log call is this
    method's only effect.
    """

    def __init__(self, log: structlog.stdlib.BoundLogger) -> None:
        self._log = log

    def from_io(self, exc: Exception, message: str, *, session_id: str = "") -> None:
        """Log ``exc`` once at ERROR with structured fields. Callers must not log it again."""
        detail = str(exc).strip()
        self._log.error(message, session_id=session_id, detail=detail)


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
        optional disambiguation hint only); this sibling skips :func:`~blizzard.runner.
        transcripts.parser.parse_turns` entirely — the caller (``sum_transcript_usage``)
        wants the raw per-message ``usage`` objects, not the panel's collapsed turns.
        """
        ...
