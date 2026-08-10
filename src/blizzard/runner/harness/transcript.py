"""The harness transcript source seam (blizzard#245) — harness-agnostic value shapes and a Protocol.

:class:`NormalizedTurn` is the turn vocabulary a source produces; a tool call's input stays structured
data, never a ``json.dumps`` string. :class:`TranscriptPosition` is **opaque to blizzard**, and
:meth:`IHarnessTranscriptSource.turns_since` reads **forward** from one, never backward or by recency.
Stdlib-only and dependency-free (``bzh:domain-core``), :class:`TranscriptErrorFactory` aside."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

import structlog

#: The normalized turn vocabulary
#: (:data:`~blizzard.runner.transcripts.repository.TurnKind` carries no thinking kind).
NormalizedTurnKind = Literal["env", "asst", "tool", "thinking"]

#: How a sidechain's attachment to its spawning tool call resolved — an open, harness-native label.
#: ``"unlinked"`` (no parent resolved at all) is the one value every harness shares.
SidechainLink = str

#: Why a *source* could not produce turns for a session — the two ways reading a file can fail.
TranscriptReadReason = Literal["not_found", "unreadable"]

#: Which raw shape a tool call's ``input`` was minted from: ``"object"`` (``input`` holds the mapping,
#: ``input_unparsed`` ``None``), ``"absent"``, ``"string"`` (held unquoted), or ``"other"`` (dumped).
ToolInputShape = Literal["object", "absent", "string", "other"]


@dataclass(frozen=True)
class TranscriptPosition:
    """An opaque forward-read cursor into a session's transcript. ``token`` is minted and interpreted
    by the harness alone — blizzard never parses it."""

    token: str


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation, structured — never flattened to a ``json.dumps`` string. ``input`` is the
    parsed mapping; ``input_unparsed`` carries a non-object input verbatim rather than discarding it;
    ``input_shape`` names which raw shape produced the pair (:data:`ToolInputShape`). ``output is None``
    while the matching result has not yet arrived — a live turn, not corruption."""

    name: str
    input: Mapping[str, Any]
    input_unparsed: str | None
    input_shape: ToolInputShape
    tool_use_id: str | None
    output: str | None
    output_truncated: bool


@dataclass(frozen=True)
class SidechainConversation:
    """A subagent's private conversation, nested under its spawning tool call.
    ``agent_id``/``agent_type`` are ``None`` when the records carry neither; ``link`` names the route
    that attached — or failed to attach — it (:data:`SidechainLink`)."""

    agent_id: str | None
    agent_type: str | None
    link: SidechainLink
    turns: list[NormalizedTurn]


@dataclass(frozen=True)
class NormalizedTurn:
    """One normalized conversation turn. ``tool``/``sidechain`` populate only on a ``kind="tool"`` turn;
    ``thinking_redacted`` is ``kind="thinking"``-only, a thinking turn carrying *presence*, not prose.
    ``truncated`` is block-level. ``index`` is batch-local and unstable across reads — every producer
    restarts it at 0, so a consumer keys and orders by position in the batch's ``turns`` list."""

    index: int
    kind: NormalizedTurnKind
    timestamp: datetime | None
    text: str
    tool: ToolCall | None
    thinking_redacted: bool
    sidechain: SidechainConversation | None
    truncated: bool


@dataclass(frozen=True)
class TranscriptBatch:
    """:meth:`IHarnessTranscriptSource.turns_since`'s return; ``available=False`` carries ``reason`` and
    empty lists, and a sidechain with no resolvable parent lands on ``unlinked_sidechains``, not turns.
    ``complete=False`` means the per-batch budget ran out first and ``next_position`` is where a caller
    resumes. ``truncated`` is the main file's own *tail* cap; ``sidechain_truncated`` is the sidecars'."""

    session_id: str
    available: bool
    reason: TranscriptReadReason | None
    turns: list[NormalizedTurn]
    unlinked_sidechains: list[SidechainConversation]
    next_position: TranscriptPosition | None
    complete: bool
    truncated: bool
    sidechain_truncated: bool
    normalizer_version: str
    harness_version: str | None


class TranscriptErrorFactory:
    """The seam's own injected error-logging seam (``bzh:dependency-inversion``). ``from_io`` is ERROR —
    the caller's read is over; ``from_io_recovered`` and ``budget_skipped`` are WARNING — the caller
    continued past it; ``not_found`` is DEBUG — no session file at all is this seam's most routine
    outcome. All under this seam's own logger name, which an operator filter must key on."""

    def __init__(self, log: structlog.stdlib.BoundLogger) -> None:
        self._log = log

    def from_io(self, exc: Exception, message: str, *, session_id: str = "") -> None:
        """Log ``exc`` once at ERROR with structured fields. Callers must not log it again."""
        detail = str(exc).strip()
        self._log.error(message, session_id=session_id, detail=detail)

    def from_io_recovered(self, exc: Exception, message: str, *, session_id: str = "", **fields: str) -> None:
        """Log ``exc`` once at WARNING: the caller is skipping this one failure and continuing its read,
        not aborting it. Callers must not log it again. ``**fields`` carries caller-specific structured
        detail as real fields rather than interpolated into ``message``."""
        detail = str(exc).strip()
        self._log.warning(message, session_id=session_id, detail=detail, **fields)

    def budget_skipped(self, message: str, *, session_id: str = "", **fields: str) -> None:
        """Log at WARNING: a shared read budget ran out before admitting something —
        no exception to carry (unlike :meth:`from_io_recovered`), but the same
        "a recoverable condition the caller continued past" class: permanent for this
        one batch, and operator-facing (a cold read's fan-out skip pairs with
        ``TranscriptBatch.sidechain_truncated``, which nothing else logs)."""
        self._log.warning(message, session_id=session_id, **fields)

    def not_found(self, *, session_id: str, **fields: str) -> None:
        """Log at DEBUG: no transcript file matched ``session_id``. ``**fields`` is
        opaque, harness-composed structured detail (Claude Code's is
        ``projects_root``) — distinguishes "wrong root" from "the agent never wrote
        one," the most likely symptom of a globbed root holding nothing, without this
        shared factory naming any one harness's own storage layout in its signature."""
        self._log.debug("transcript not found", session_id=session_id, **fields)


class IHarnessTranscriptSource(Protocol):
    """The per-harness transcript source seam. Three operations, all reads: ``turns_since`` collapses
    the harness's raw session records into :class:`NormalizedTurn`\\ s, reading forward from ``since``
    (``None`` for "from the start"); ``read_raw_lines``/``size_bytes`` sit here too, so the
    file-location knowledge this seam carries is never duplicated outside it."""

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        """Normalized turns written since ``since``, or from the start when ``None``. ``spawn_cwd`` is an
        optional disambiguation hint — never the lookup key, used only to break a multi-match tie."""
        ...

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        """The session's raw transcript lines, unparsed — empty when none exist or the
        file is unreadable (the envelope-less usage fallback's own read)."""
        ...

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """The session transcript's size on disk, or ``None`` when it cannot be read —
        an *unknown*, never a zero, so an unmeasurable transcript never reads as
        "well under bound" (the rotation check's own signal)."""
        ...


#: The normalizer-version stamp a batch that never ran a real normalizer carries — the
#: null source's own, distinct from any harness-specific normalizer's version string.
_NO_NORMALIZER_VERSION = ""


class NullTranscriptSource:
    """The transcript source bound when a harness has no on-disk transcript concept, or a composition
    site has not wired a real one. Every read behaves like an absent-but-healthy transcript, so a
    caller needs no null check of its own."""

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        return TranscriptBatch(
            session_id=session_id,
            available=False,
            reason="not_found",
            turns=[],
            unlinked_sidechains=[],
            next_position=None,
            complete=True,
            truncated=False,
            sidechain_truncated=False,
            normalizer_version=_NO_NORMALIZER_VERSION,
            harness_version=None,
        )

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return []

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None


# Typecheck-time Protocol conformance sentinel (the exemplar's shape): pyright rejects the return if
# `NullTranscriptSource` drifts from `IHarnessTranscriptSource`.
def _conforms_harness_transcript_source(x: NullTranscriptSource) -> IHarnessTranscriptSource:
    return x
