"""The Claude Code ``IHarnessTranscriptSource`` adapter (blizzard#245).

The **only** module in this pair that touches ``pathlib``/``glob`` I/O
(``bzh:dependency-inversion``); confined to ``internal/``. Owns the file-location
knowledge no other module in the tree holds — ``mangle_cwd`` disambiguation, the
session-id glob, the multi-match tie-break, the tail-seek read — declared here
directly rather than imported from elsewhere. :mod:`blizzard.runner.transcripts` holds
only the panel's read-model types (``repository.py``), its HTTP service, and
:mod:`~blizzard.runner.transcripts.internal.projected_transcript_repository`, the sole
implementation of that read model — a projection over :class:`ClaudeCodeTranscriptSource`
below.

Adds what ``transcripts/`` never needed: sidecar-file discovery (the corpus-primary
sidechain shape — real sessions overwhelmingly join a subagent's conversation through
its own sidecar file, not an inline ``uuid``/``parentUuid`` run; see
``claude_code_normalizer.py``'s own sidechain-linking-order docstring) under
``<project-dir>/<session-id>/subagents/agent-<agentId>.jsonl``, the agent-id join (link
route 1) against
:attr:`~blizzard.runner.harness.internal.claude_code_normalizer.NormalizedFile.agent_id_by_tool_turn`'s
candidates, and :class:`~blizzard.runner.harness.transcript.TranscriptPosition` minting/reading
for forward incremental reads.

**Read shape** (this module's own cap accounting — :data:`MAX_FILE_BYTES` and
:data:`MAX_BATCH_BYTES` below):

* ``since=None`` — a cold read. Each file involved (the main session file, and any
  sidecar discovered from a candidate spawn) is seeked to its own tail — the last
  :data:`MAX_FILE_BYTES` — and read to EOF uncapped by :data:`MAX_BATCH_BYTES`. A
  tail seek can land mid-line/mid-codepoint, so the first split fragment is
  discarded (``errors="replace"`` covers a split UTF-8 codepoint at the boundary).
  ``TranscriptBatch.truncated`` reflects this cap on the main file only;
  ``TranscriptBatch.sidechain_truncated`` reflects the same cap on any sidecar, plus
  the sidecar fan-out's own shared budget running out — all three conditions are
  cold-read-only; the forward path's own budget exhaustion is reported via
  ``complete``, never either truncation field.
* ``since=<position>`` — a forward read starting at that exact byte offset (always a
  newline boundary, by construction — see below), bounded by one shared
  :data:`MAX_BATCH_BYTES` budget spent across the main file first, then whatever
  sidecars a route-1 candidate names, in that order; a sidecar the budget never
  reaches keeps its ``since`` offset unchanged in the minted ``next_position``, and
  the batch reports ``complete=False``. A read that lands mid-line (the file was
  live-appended-to) holds that trailing partial fragment back rather than consuming
  it, so ``next_position`` never lands anywhere but a newline boundary and a
  genuinely truncated final record is simply re-read complete on the next call —
  except when a single record is itself at least as wide as the whole budget, which
  a budget-bound window can never wait out; that record is consumed (and, if
  malformed, dropped) so forward progress is never permanently stalled.

**Sidecar candidacy survives a batch boundary; the spawning turn's identity does
not.** This is a forward-read (``since=<position>``) guarantee — a cold
(``since=None``) call makes no incremental-retry promise at all, so a sidecar its
shared budget never reaches is simply absent from that one batch, not carried
anywhere. On the forward path: a sidecar's agent id, once seen (discovered fresh, or
named in the incoming ``since`` position), stays a read candidate on every later call
regardless of which batch's main-file lines first mentioned it — ``next_position``
always names every sidecar this call knows about, budget-skipped ones included, so one
never falls out of consideration just because its spawning line scrolled out of the
read window. But the spawning tool-call *turn* is only ever reachable while it is
still part of *this* call's own ``normalized.turns`` — once delivered in an earlier
batch, this module holds no reference back to it to amend. A sidecar resolved in a
later batch than its spawning turn therefore lands on
:attr:`~blizzard.runner.harness.transcript.TranscriptBatch.unlinked_sidechains`
instead of nested — its conversation still surfaces, just not under the tool call
that spawned it.

The same batch-locality applies, undressed, to a ``tool_result`` whose matching
``tool_use`` fell in an earlier batch:
:func:`~blizzard.runner.harness.internal.claude_code_normalizer.normalize_lines` only
ever sees one call's own lines, so neither the pending-tool index (route to attaching
output) nor the uuid-chain sidechain-link route (route 2) carries across a call
boundary. Both degrade rather than crash — an unmatched result stays absent
(:attr:`~blizzard.runner.harness.transcript.ToolCall.output` stays ``None``, its own
documented "not yet arrived" shape) and an uuid-chain split falls through to route 3
and then ``unlinked`` — but
neither is fixed up once the correlating record does arrive. A forward multi-batch
read is therefore complete-eventually for a *sidecar's own conversation content*
(never permanently dropped), but not for re-attaching a tool call's own output or a
route-2 sidechain link once that tool call's turn has already been delivered in a
prior batch: an accepted gap, not yet closed, tracked against `epic:transcripts`'s
own delta-shipping consumer work rather than papered over here.

:class:`~blizzard.runner.harness.transcript.TranscriptPosition`'s token is this
module's own JSON: ``{"main": <byte offset>, "sidecars": {<agentId>: <byte offset>}}``
— opaque to every other caller, per the seam's contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from blizzard.runner.harness.internal.claude_code_normalizer import NORMALIZER_VERSION, normalize_lines
from blizzard.runner.harness.transcript import (
    IHarnessTranscriptSource,
    NormalizedTurn,
    SidechainConversation,
    TranscriptBatch,
    TranscriptErrorFactory,
    TranscriptPosition,
    TranscriptReadReason,
)

#: Refuse a pathological file on a cold (``since=None``) read: only the last
#: this-many bytes are read off disk at all, enforced by seeking to the tail before
#: reading — bytes read off disk for any ONE file this call touches (the main session
#: file, or any single sidecar) is bounded by this cap regardless of that file's own
#: size. **Not**, by itself or in combination with :data:`MAX_BATCH_BYTES` below, a
#: bound on this call's peak *in-memory* footprint:
#: :func:`~blizzard.runner.harness.internal.claude_code_normalizer.normalize_lines`
#: materializes every parsed record into a list before collapsing it, an unconditional
#: multiplier over raw bytes read (shape-dependent, measured 4-5x on real
#: Claude-Code-shaped content) neither this
#: cap nor that one accounts for. Both remain real, useful bounds on bytes read and
#: on sidecar fan-out *count* — read that claim, not a peak-memory one. The value
#: coincides with `epic:transcripts`'s declared **chunk** transcript budget (also
#: 64 MB) but implements no plan cap: that budget is enforced at the runner/hub
#: shipping boundary a later lane adds, spans a whole chunk's every session and
#: segment, and is unrelated to this per-*file* tail cap — one chunk can still touch
#: several of these caps. Chosen against the measured ceiling instead: a real
#: session's max observed size is ~5.3 MB and rotation caps a live one at 50 MB, so
#: this is comfortably above any one file this seam reads today.
MAX_FILE_BYTES = 64 * 1024 * 1024

#: Bounds one forward (``since`` given) read's total bytes read off disk across the
#: main file and every sidecar it reaches — a delta batch is never unbounded just
#: because a fleet worker went quiet for a long stretch. Exhausting it reports
#: ``complete=False`` plus a ``next_position`` the caller loops on. Also spent,
#: independently, as a cold (``since=None``) read's shared sidecar fan-out budget:
#: each sidecar a cold call reads still keeps its own individual :data:`MAX_FILE_BYTES`
#: tail cap, but the *number* of sidecars this call reads at all is gated by this
#: budget too — without it, a session with many sidecars could read ``len(sidecars) *
#: MAX_FILE_BYTES`` bytes off disk at once, unbounded in the sidecar count (see
#: :data:`MAX_FILE_BYTES` above for why "bytes read" and "peak memory" are not the
#: same claim). A cold read that runs out
#: of this budget mid-fan-out reports the shortfall via ``TranscriptBatch.truncated``.
#: A cold call still mints a ``next_position`` like any other (every call does), but
#: today's sole consumer — the panel projection — discards it rather than looping on
#: it; a budget-skipped sidecar is recorded into it anyway (see below), so a future
#: forward lane that bootstraps via a cold read can still find it.
#: The gate is checked before a sidecar is admitted, not debited as it reads — a
#: sidecar admitted with one byte of budget left is still read at its own full
#: :data:`MAX_FILE_BYTES` tail cap, so the real worst-case bound on one cold call's
#: sidecar fan-out is ``MAX_BATCH_BYTES + MAX_FILE_BYTES``, not a hard
#: ``MAX_BATCH_BYTES`` ceiling — what this budget actually gates is the sidecar
#: *count*, never letting it grow unbounded, not a byte-precise total.
MAX_BATCH_BYTES = 8 * 1024 * 1024


def mangle_cwd(cwd: str) -> str:
    """Claude Code's project-directory name for the absolute spawn cwd ``cwd``.

    The transform (``/`` → ``-``) is undocumented third-party behavior and only
    partly verified — ``.``/``_`` mangling is unobserved, and the transform is
    lossy (``/a/b-c`` and ``/a-b/c`` mangle identically) — so it is **not** the
    primary transcript lookup (:meth:`ClaudeCodeTranscriptSource.turns_since`
    below globs by session id instead, which is immune to this ambiguity). Kept
    only as the multi-match disambiguator, and as the one place this rule lives.
    """
    return cwd.replace("/", "-")


@dataclass(frozen=True)
class _DecodedPosition:
    main: int
    sidecars: dict[str, int]


def _decode_position(since: TranscriptPosition | None) -> _DecodedPosition:
    """A tolerant decode: a foreign or malformed token degrades to "start over"
    rather than raising — a position is a hint this module minted for itself, and a
    corrupt one is never a caller's fault to crash over."""
    if since is None:
        return _DecodedPosition(main=0, sidecars={})
    try:
        data = json.loads(since.token)
    except (json.JSONDecodeError, TypeError):
        return _DecodedPosition(main=0, sidecars={})
    if not isinstance(data, dict):
        return _DecodedPosition(main=0, sidecars={})
    main = data.get("main")
    sidecars_raw = data.get("sidecars")
    # A negative offset is as malformed as a missing/wrong-typed one — clamped to 0
    # (not raised past `f.seek()`) on the same tolerant "start over" path, never a
    # `f.seek()`-raised `OSError` for what is still just a corrupt hint.
    sidecars = (
        {k: max(v, 0) for k, v in sidecars_raw.items() if isinstance(k, str) and isinstance(v, int)}
        if isinstance(sidecars_raw, dict)
        else {}
    )
    return _DecodedPosition(main=max(main, 0) if isinstance(main, int) else 0, sidecars=sidecars)


def _encode_position(main_offset: int, sidecar_offsets: dict[str, int]) -> TranscriptPosition:
    return TranscriptPosition(token=json.dumps({"main": main_offset, "sidecars": sidecar_offsets}, sort_keys=True))


@dataclass(frozen=True)
class _FileRead:
    lines: list[str]
    next_offset: int
    truncated: bool  # the tail cap tripped (`since=None` only)
    hit_budget: bool  # more remained beyond what this call read (forward reads only)


def _read_cold(path: Path) -> _FileRead:
    """``since=None``: tail-seek bound by :data:`MAX_FILE_BYTES`, read to EOF."""
    size = path.stat().st_size
    truncated = size > MAX_FILE_BYTES
    begin = size - MAX_FILE_BYTES if truncated else 0
    with path.open("rb") as f:
        if truncated:
            f.seek(begin)
        raw = f.read()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if truncated and lines:
        lines = lines[1:]  # a mid-file seek can land mid-line — drop the fragment
    return _FileRead(lines=lines, next_offset=size, truncated=truncated, hit_budget=False)


def _read_forward(path: Path, *, start_offset: int, budget: int) -> _FileRead:
    """A forward read from ``start_offset``, bounded by ``budget`` bytes.

    ``budget <= 0`` makes no progress at all (the shared batch budget was already
    spent by an earlier file this call) — reported via ``hit_budget`` so the caller
    knows this file still has unread content waiting.
    """
    size = path.stat().st_size
    begin = min(start_offset, size)
    if budget <= 0:
        return _FileRead(lines=[], next_offset=begin, truncated=False, hit_budget=begin < size)
    end = min(begin + budget, size)
    with path.open("rb") as f:
        f.seek(begin)
        raw = f.read(end - begin)
    hit_budget = end < size
    last_newline = raw.rfind(b"\n")
    # Consume up to the last complete line; a trailing partial fragment (the file is
    # live-appended-to) is held back so `next_position` never lands mid-line — it is
    # simply re-read complete on a later call. A window with NO newline at all splits
    # on whether the budget itself was the binding constraint. Reading a FULL budget's
    # worth of bytes with no newline anywhere in it (`hit_budget`) proves the current
    # record is at least `budget` bytes wide — waiting can never resolve this, since a
    # later call reads the identical window from the identical `begin` and finds the
    # identical absence, so this is the escape that guarantees monotonic forward
    # progress: consume the window whole, accepting the vanishingly rare cost of
    # silently dropping one oversized record (`normalize_lines` can't parse a
    # truncated fragment) rather than stalling forever. Short of the budget — the
    # window reached the file's own current end with room to spare — is the ordinary
    # live-appended case: the trailing fragment may simply not be flushed yet, so this
    # window makes zero progress instead and is read again, complete, once the writer
    # catches up.
    if last_newline != -1:
        consumed = last_newline + 1
    elif hit_budget:
        consumed = len(raw)
    else:
        consumed = 0
    lines = raw[:consumed].decode("utf-8", errors="replace").splitlines()
    return _FileRead(lines=lines, next_offset=begin + consumed, truncated=False, hit_budget=hit_budget)


def _agent_type(turns: list[NormalizedTurn], index: int) -> str | None:
    tool = turns[index].tool
    if tool is None:
        return None
    candidate = tool.input.get("subagent_type")
    return candidate if isinstance(candidate, str) else None


class ClaudeCodeTranscriptSource:
    """Locates and normalizes a session's transcript (plus its sidecars) under
    ``projects_root``.

    ``projects_root`` is a constructor argument (``bzh:dependency-injection``) —
    empty-string defaulting to ``~/.claude/projects`` is resolved once at the
    composition root, never here — so a test injects ``tmp_path`` directly:
    hermetic by construction, no ``HOME`` monkey-patching.
    """

    def __init__(self, projects_root: str, error_factory: TranscriptErrorFactory) -> None:
        self._projects_root = Path(projects_root)
        self._errors = error_factory

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        matches = sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))
        if not matches:
            self._errors.not_found(session_id=session_id, projects_root=str(self._projects_root))
            return _unavailable(session_id, "not_found")

        try:
            path = matches[0] if len(matches) == 1 else self._disambiguate(matches, spawn_cwd)
            position = _decode_position(since)
            # A decoded offset past the file's actual current size is as malformed as a
            # negative one for this call's purposes (the file was truncated or replaced
            # since the position was minted) — `_read_forward`'s own `min(start_offset,
            # size)` clamp would otherwise make its `next_offset - start_offset` delta
            # negative, inflating `remaining_budget` past `MAX_BATCH_BYTES` by the
            # shortfall. Treated the same tolerant way as any other corrupt hint: start
            # this file over from 0 rather than spend an uncapped budget against it.
            if position.main > path.stat().st_size:
                position = replace(position, main=0)
            if since is None:
                main_read = _read_cold(path)
                # A cold read's sidecar fan-out shares this one budget too — the same
                # value the forward path already shares across the main file plus its
                # sidecars, spent here purely as a gate on how many sidecars a
                # pathological session's cold read pulls in, never on how much of any
                # one of them (each individual sidecar keeps its own MAX_FILE_BYTES tail
                # cap). Without it, bytes read off disk for a cold read's sidecar
                # fan-out was every discovered sidecar's own MAX_FILE_BYTES
                # simultaneously — unbounded in the sidecar count.
                remaining_budget = MAX_BATCH_BYTES
            else:
                main_read = _read_forward(path, start_offset=position.main, budget=MAX_BATCH_BYTES)
                remaining_budget = MAX_BATCH_BYTES - (main_read.next_offset - position.main)
        except OSError as exc:
            self._errors.from_io(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return _unavailable(session_id, "unreadable")

        normalized = normalize_lines(main_read.lines)
        hit_budget = main_read.hit_budget
        sidecar_budget_exhausted = False
        sidecar_truncated = False
        sidecar_offsets = dict(position.sidecars)

        sidecar_dir = path.parent / session_id / "subagents"
        # Candidates are the union of this batch's own discoveries and every agent id a
        # PRIOR position already named — never just the former: a spawning tool call
        # that landed in an earlier batch (its turn already delivered, and this batch's
        # `normalized.turns` never contains it) still has its sidecar carried forward as
        # a candidate here, rather than silently falling out of consideration the moment
        # its main-file line scrolls out of the current read window.
        index_by_agent_id: dict[str, list[int]] = {}
        for index, aid in normalized.agent_id_by_tool_turn.items():
            index_by_agent_id.setdefault(aid, []).append(index)
        candidate_agent_ids = sorted(set(index_by_agent_id) | set(position.sidecars))
        for agent_id in candidate_agent_ids:
            sidecar_path = sidecar_dir / f"agent-{agent_id}.jsonl"
            if not sidecar_path.is_file():
                # Not yet flushed to disk — still recorded as a live candidate (same as
                # a budget-skipped one below) so a later call, once the file exists, can
                # find it: on the forward path directly, on the cold path via a future
                # forward lane that bootstraps from this call's own minted position.
                sidecar_offsets.setdefault(agent_id, 0)
                continue
            if remaining_budget <= 0:
                if since is not None:
                    hit_budget = True
                else:
                    # Flagged via `truncated` too — the same signal the main file's own
                    # tail cap already uses — so a cold caller sees this batch was
                    # incomplete even though it never loops on `next_position` itself.
                    sidecar_budget_exhausted = True
                    self._errors.budget_skipped(
                        "sidecar transcript skipped: shared fan-out budget exhausted",
                        session_id=session_id,
                        agent_id=agent_id,
                    )
                # A budget-skipped sidecar stays a live candidate regardless of path:
                # recording it here, even at offset 0, is what keeps it from falling out
                # of `next_position` entirely — on the forward path for the very next
                # call, and on the cold path for a future forward lane that bootstraps
                # from this cold read's own minted position.
                sidecar_offsets.setdefault(agent_id, 0)
                continue

            try:
                if since is None:
                    sidecar_size = sidecar_path.stat().st_size
                    sidecar_read = _read_cold(sidecar_path)
                    remaining_budget -= min(sidecar_size, MAX_FILE_BYTES)
                else:
                    start = sidecar_offsets.get(agent_id, 0)
                    # Same past-EOF clamp as the main file above — a stale offset from a
                    # truncated/replaced sidecar would otherwise inflate `remaining_budget`
                    # via the same negative-delta path.
                    if start > sidecar_path.stat().st_size:
                        start = 0
                    sidecar_read = _read_forward(sidecar_path, start_offset=start, budget=remaining_budget)
                    remaining_budget -= sidecar_read.next_offset - start
            except OSError as exc:
                # Recovered, not aborted: this one sidecar is skipped, but the batch
                # this call is building still reports `available=True` — WARNING, not
                # the boundary-failure ERROR the main-file open failures below use.
                self._errors.from_io_recovered(
                    exc, "sidecar transcript unreadable", session_id=session_id, agent_id=agent_id
                )
                continue

            hit_budget = hit_budget or sidecar_read.hit_budget
            sidecar_truncated = sidecar_truncated or sidecar_read.truncated
            sidecar_offsets[agent_id] = sidecar_read.next_offset
            sidecar_normalized = normalize_lines(sidecar_read.lines, is_sidechain_file=True)
            spawning_indices = index_by_agent_id.get(agent_id)
            if not spawning_indices:
                # The spawning tool call was delivered in an earlier batch — nothing in
                # `normalized.turns` to nest under here, so the conversation still
                # surfaces (never silently dropped) via the unlinked list instead.
                # `link="unlinked"`, not `"agent-id"`: everything the normalizer itself
                # puts on this list carries `"unlinked"`, and a consumer switching on
                # `link` to tell resolved from unresolved needs that to hold for every
                # producer landing here, not just the normalizer's own.
                if sidecar_normalized.turns:
                    normalized.unlinked_sidechains.append(
                        SidechainConversation(
                            agent_id=agent_id, agent_type=None, link="unlinked", turns=sidecar_normalized.turns
                        )
                    )
                # `sidecar_normalized.unlinked_sidechains` is always empty here:
                # `normalize_lines(..., is_sidechain_file=True)` never routes a record
                # into its own `sidechain_records` bucket, so nothing ever populates it.
                continue
            for index in spawning_indices:
                already_attached = normalized.turns[index].sidechain
                if already_attached is not None:
                    # An inline run (route 2/3) already resolved onto this turn before
                    # this source module ever ran. The sidecar join (route 1) is
                    # preferred — the corpus-primary shape — but the displaced inline
                    # conversation still needs a home rather than being replaced in
                    # place and lost: it surfaces on the unlinked list instead, same as
                    # any other sidechain this call can't nest.
                    normalized.unlinked_sidechains.append(already_attached)
                normalized.turns[index] = replace(
                    normalized.turns[index],
                    sidechain=SidechainConversation(
                        agent_id=agent_id,
                        agent_type=_agent_type(normalized.turns, index),
                        link="agent-id",
                        turns=sidecar_normalized.turns,
                    ),
                )

        complete = True if since is None else not hit_budget
        next_position = _encode_position(main_read.next_offset, sidecar_offsets)
        return TranscriptBatch(
            session_id=session_id,
            available=True,
            reason=None,
            turns=normalized.turns,
            unlinked_sidechains=normalized.unlinked_sidechains,
            next_position=next_position,
            complete=complete,
            truncated=main_read.truncated,
            sidechain_truncated=sidecar_budget_exhausted or sidecar_truncated,
            normalizer_version=NORMALIZER_VERSION,
            harness_version=normalized.harness_version,
        )

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        matches = sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))
        if not matches:
            return []
        try:
            path = matches[0] if len(matches) == 1 else self._disambiguate(matches, spawn_cwd)
            return _read_cold(path).lines
        except OSError as exc:
            # Recovered, not a boundary failure: both callers (the envelope-less usage
            # fallback, the rotation size check) treat an empty/`None` reply as "no
            # signal" and continue past it rather than aborting — WARNING, not ERROR.
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return []

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """``stat().st_size`` on the located transcript, or ``None`` when there is none.

        Deliberately a ``stat``, not a read: the file this measures is the one that
        has grown too large to keep resuming into."""
        matches = sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))
        if not matches:
            return None
        try:
            path = matches[0] if len(matches) == 1 else self._disambiguate(matches, spawn_cwd)
            return path.stat().st_size
        except OSError as exc:
            # Recovered, not a boundary failure — see `read_raw_lines` above.
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return None

    @staticmethod
    def _disambiguate(matches: list[Path], spawn_cwd: str | None) -> Path:
        """Multi-match tie-break: the spawn-cwd hint, else newest by mtime."""
        if spawn_cwd:
            wanted = mangle_cwd(spawn_cwd)
            for match in matches:
                if match.parent.name == wanted:
                    return match
        return max(matches, key=lambda p: p.stat().st_mtime)


def _unavailable(session_id: str, reason: TranscriptReadReason) -> TranscriptBatch:
    return TranscriptBatch(
        session_id=session_id,
        available=False,
        reason=reason,
        turns=[],
        unlinked_sidechains=[],
        next_position=None,
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version=NORMALIZER_VERSION,
        harness_version=None,
    )


# Typecheck-time Protocol/adapter conformance sentinel (the exemplar's shape,
# `blizzard-context:/exemplars/python/repo_pattern.py`). Pyright rejects the return if
# `ClaudeCodeTranscriptSource` drifts from `IHarnessTranscriptSource`.
def _conforms_harness_transcript_source(x: ClaudeCodeTranscriptSource) -> IHarnessTranscriptSource:
    return x
