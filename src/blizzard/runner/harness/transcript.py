"""The harness transcript source seam (blizzard#245).

Where a session's raw conversation lives and what its records mean is harness-specific
knowledge — Claude Code's ``~/.claude/projects/<mangled-cwd>/<session-id>.jsonl`` layout
is nothing an opencode or codex adapter shares. This module is the harness-agnostic
value shape and Protocol that knowledge sits behind
(:meth:`~blizzard.runner.harness.adapter.IHarnessAdapter.transcript_source`), the shape
:mod:`.usage` and :mod:`.fingerprint` already have. The value shapes, the Protocol, and
:class:`NullTranscriptSource` are stdlib-only, dependency-free (``bzh:domain-core``) —
:class:`TranscriptErrorFactory` is the one deliberate exception living alongside them
(the ``structlog`` import below is entirely its own): error logging on a source read
failure is shared, harness-agnostic infrastructure in exactly the same sense the
Protocol above it is (any future per-harness source reads files and can fail the same
way), so it sits here rather than duplicated into each harness's own ``internal/``
adapter — the same co-location the exemplar's own ``RepoErrorFactory``
(``blizzard-context:/exemplars/python/repo_pattern.py``) uses for a Protocol and its
injected error-wrapping seam, not a gap against it. :class:`NullTranscriptSource`
below is likewise kept at this feature-package root rather than under
``internal/`` — it is not per-harness (every harness shares the one "nothing wired"
shape), so it has no adapter identity to confine.

:class:`NormalizedTurn` is the turn vocabulary a per-harness source produces: ``env``/
``asst``/``tool`` (the panel's existing three) plus ``thinking`` — a kind the current
transcript parser drops entirely. A tool call's input stays **structured data**
(:attr:`ToolCall.input`, a ``Mapping``), never a ``json.dumps`` string — the shape a
future analytics consumer queries. A tool call that spawned a subagent nests that
subagent's own turns on it (:attr:`NormalizedTurn.sidechain`), linked by whichever route
resolved it (:data:`SidechainLink`) — a sidechain whose parent could not be resolved is
still conversation, carried on :attr:`TranscriptBatch.unlinked_sidechains` rather than
dropped.

:class:`TranscriptPosition` is **opaque to blizzard**: the harness mints and interprets
it, so a codex/opencode source (``epic:adapters``) is free to use a shape that is not a
byte offset. :meth:`IHarnessTranscriptSource.turns_since` reads **forward** from a
position — the operation a later issue builds an outbound lane on — never backward or
by recency; a batch that could not read everything asked reports ``complete=False`` plus
a ``next_position`` the caller loops on.

A harness with no on-disk transcript at all (or a source not yet wired at a given
composition site) binds :class:`NullTranscriptSource` rather than making every caller
handle ``None`` — the same precedent
:meth:`~blizzard.runner.harness.adapter.IHarnessAdapter.resolve_effort`/
:meth:`~blizzard.runner.harness.adapter.IHarnessAdapter.sample_external_subscription_usage`
set for "this harness has no such knob", expressed as a binding.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

import structlog

#: The normalized turn vocabulary. ``thinking`` sits alongside the panel's prior
#: three (``env``/``asst``/``tool``), which carried no thinking-block kind at all.
NormalizedTurnKind = Literal["env", "asst", "tool", "thinking"]

#: How a sidechain conversation's attachment to its spawning tool call was resolved,
#: carried as data rather than left for a reader to guess at fidelity — an open,
#: harness-native label (the same open-string treatment
#: :attr:`~blizzard.runner.harness.external_usage.ExternalSubscriptionUsageWindow.window`
#: gives a harness-native vocabulary word, rather than a blizzard-defined enum a second
#: adapter would have to satisfy). The one value every harness is expected to share is
#: ``"unlinked"`` — no route resolved a parent at all — since that resolved/unresolved
#: distinction is the one thing blizzard itself acts on; every other value is that
#: harness's own route name. Claude Code currently mints ``"agent-id"`` (an exact join,
#: a sidecar file's name/records to the spawning call's ``toolUseResult.agentId``),
#: ``"uuid-chain"``, and ``"prompt-timestamp"`` (its two inline-layout fallbacks).
SidechainLink = str

#: Why a *source* could not produce turns for a session — the two reasons reading a
#: file can fail. Deliberately narrower than
#: :data:`~blizzard.runner.transcripts.repository.TranscriptUnavailable`: ``"spawning"``
#: (a lease with no session id yet) is the panel service's own concept, not a source's,
#: so it is never a member here — the panel projection widens into it instead.
TranscriptReadReason = Literal["not_found", "unreadable"]

#: Which raw shape a tool call's ``input`` was minted from — the discriminator a
#: re-materializing consumer (the panel projection) needs to reproduce the wire
#: contract's blanket ``json.dumps(raw_input)`` exactly, rather than guessing from
#: ``input``/``input_unparsed`` alone (ambiguous: an absent input and an empty
#: object both leave ``input_unparsed`` ``None``; a bare string that happens to
#: itself parse as JSON is indistinguishable from an already-serialized one).
#: ``"object"`` — ``input`` holds the real mapping, ``input_unparsed`` is ``None``.
#: ``"absent"`` — the record carried no ``input`` (or an explicit JSON ``null``);
#: the wire contract renders this as ``""``, not ``"{}"``. ``"string"`` — the record's
#: ``input`` was itself a bare JSON string, held verbatim (unquoted) on
#: ``input_unparsed``; re-materializing it needs an explicit ``json.dumps`` to
#: match the wire contract's quoting. ``"other"`` — any other non-object value (a
#: list, a number, a bool); ``input_unparsed`` already holds its final
#: ``json.dumps`` form, emitted verbatim.
ToolInputShape = Literal["object", "absent", "string", "other"]


@dataclass(frozen=True)
class TranscriptPosition:
    """An opaque forward-read cursor into a session's transcript.

    ``token`` is minted and interpreted by the harness alone — blizzard never parses
    it. Claude Code's is JSON of per-file byte offsets (the main file plus each
    discovered sidecar); a future harness's is whatever shape fits its own storage.
    """

    token: str


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation, structured — never flattened to a ``json.dumps`` string.

    ``input`` is the parsed mapping a consumer (the analytics contract this issue
    exists for) queries directly; ``input_unparsed`` carries a non-object input
    verbatim instead of coercing it into an empty mapping, so a malformed or
    scalar ``input`` is never silently discarded. ``input_shape`` names which raw
    shape produced the pair (:data:`ToolInputShape`) — the explicit discriminator a
    re-materializing consumer needs, since ``input``/``input_unparsed`` alone are
    ambiguous over two independent shape questions. ``output``/``output_truncated``
    mirror the panel's existing tool-result shape: ``output is None`` while the
    matching result has not yet arrived in the file (a live turn, not corruption).
    """

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

    ``agent_id``/``agent_type`` are read off the sidecar or inline records when
    present, else ``None`` — a fallback-linked or unlinked sidechain may carry
    neither. ``link`` names the route that attached (or failed to attach) it; see
    :data:`SidechainLink`.
    """

    agent_id: str | None
    agent_type: str | None
    link: SidechainLink
    turns: list[NormalizedTurn]


@dataclass(frozen=True)
class NormalizedTurn:
    """One normalized conversation turn.

    ``tool``/``sidechain`` are populated only on a ``kind="tool"`` turn — a tool
    call, and (when it spawned a subagent) that subagent's nested conversation.
    ``thinking_redacted`` is ``kind="thinking"``-only: Claude Code redacts thinking
    content universally, so a thinking turn carries *presence*, not prose, as the
    expected shape rather than an edge case. ``truncated`` is block-level, mirroring
    :attr:`~blizzard.runner.transcripts.repository.Turn.truncated`. ``index`` is
    batch-local and unstable across reads — every producer restarts it at 0 for each
    call, so two forward batches of one session both contain a turn 0; a delta-shipping
    consumer keys and orders by position in a batch's own ``turns`` list, not this field.
    """

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
    """:meth:`IHarnessTranscriptSource.turns_since`'s return.

    ``available=False`` carries ``reason`` and empty ``turns``/``unlinked_sidechains``,
    mirroring :class:`~blizzard.runner.transcripts.repository.Transcript`.
    ``unlinked_sidechains`` is a dedicated field (not folded into ``turns``) because a
    :class:`SidechainConversation` is otherwise reachable only through a tool turn's
    ``.sidechain`` — with no spawning call to nest under, it would otherwise have to
    land among the ordinary top-level turns and misstate its provenance.

    ``next_position``/``complete`` are the forward-read contract: ``complete=True``
    means every turn since ``since`` was read into this batch; ``complete=False`` means
    the source's per-batch budget was exhausted first, and ``next_position`` is where a
    caller resumes. ``truncated`` reflects the *tail* cap on the **main file only** — a
    first, ``since=None`` read of a pathological session — distinct from ``complete``,
    which reflects the *forward* batch-budget cap. ``sidechain_truncated`` is the
    parallel signal for sidecar content alone (a sidecar's own tail cap, or the shared
    sidecar fan-out budget running out on a cold read): kept off ``truncated`` on
    purpose, since a narrowing consumer that never renders a sidechain at all (today's
    panel) would otherwise report a positive, operator-facing truncation banner for
    content it never shows and never cut.

    ``normalizer_version``/``harness_version`` stamp every batch: the former names the
    normalizer code that produced it (bumped when output shape or semantics change, so
    a future better normalizer's rows are told apart from this one's), the latter the
    harness build that wrote the source records, or ``None`` when no record carried one.
    """

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
    """The harness seam's own injected error-logging seam (``bzh:dependency-inversion``'s
    factory-injected error pattern — the exemplar's per-transport ``from_<transport>``
    shape, widened here to ``not_found``: a lookup-miss carrying no exception, DEBUG
    rather than a wrapped error).

    ``from_io`` is a boundary failure: the caller's read is over, transformed into
    ``TranscriptBatch.available=False`` (or an empty/``None`` reply on the
    ``read_raw_lines``/``size_bytes`` siblings) — ERROR per ``bzh:structlog-logging``.
    ``from_io_recovered`` is for a caller that reads on regardless (one sidecar among
    several failed to open; the batch it belongs to still reports
    ``available=True``) — WARNING, the same convention's "a recoverable condition the
    caller continued past." ``not_found`` is DEBUG: no session file at all is this
    seam's most routine outcome (a lease with no transcript yet), surfaced under this
    seam's own logger name (``blizzard.runner.harness.transcript``, bound by the
    composition root) — an operator filter must be keyed on that name to match it.
    """

    def __init__(self, log: structlog.stdlib.BoundLogger) -> None:
        self._log = log

    def from_io(self, exc: Exception, message: str, *, session_id: str = "") -> None:
        """Log ``exc`` once at ERROR with structured fields. Callers must not log it again."""
        detail = str(exc).strip()
        self._log.error(message, session_id=session_id, detail=detail)

    def from_io_recovered(self, exc: Exception, message: str, *, session_id: str = "", **fields: str) -> None:
        """Log ``exc`` once at WARNING: the caller is skipping this one failure and
        continuing its read, not aborting it. Callers must not log it again.
        ``**fields`` carries any caller-specific structured detail (e.g. the sidecar's
        own ``agent_id``) as a real field rather than interpolated into ``message`` —
        never a harness-specific keyword this shared factory's own signature would
        have to name."""
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
    """The per-harness transcript source seam. Three operations, all reads.

    ``turns_since`` is the normalization operation (blizzard#245): the harness's raw
    session records collapsed into :class:`NormalizedTurn`\\ s, reading forward from
    ``since`` (``None`` for "from the start"). ``read_raw_lines``/``size_bytes`` are
    pre-existing operations relocated behind this seam, not new surface — the
    envelope-less usage fallback and the transcript-rotation signal both need the same
    file-location knowledge this seam already carries, and leaving them on the old
    repository would keep that knowledge duplicated on both sides of it.
    """

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        """Normalized turns written since ``since``, or from the start when ``None``.

        ``spawn_cwd`` is the same optional disambiguation hint
        :meth:`~blizzard.runner.transcripts.repository.IReadTranscriptRepository.read_turns`
        takes — never the lookup key, used only to break a multi-match tie.
        """
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
    """The transcript source bound when a harness has no on-disk transcript concept
    (or a composition site has not wired a real one — see ``ClaudeCodeAdapter``'s
    default). Every read behaves exactly like today's absent-but-healthy transcript,
    so a caller needs no null check of its own."""

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


# Typecheck-time Protocol/adapter conformance sentinel (the exemplar's shape,
# `blizzard-context:/exemplars/python/repo_pattern.py`). Pyright rejects the return if
# `NullTranscriptSource` drifts from `IHarnessTranscriptSource`.
def _conforms_harness_transcript_source(x: NullTranscriptSource) -> IHarnessTranscriptSource:
    return x
