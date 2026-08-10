"""The transcript lane's per-tick pump (issue #246) — advances each live segment's
forward-read cursor through ``IHarnessTranscriptSource.turns_since`` and enqueues its
record(s) atomically with the cursor write (D3), one-or-more of blizzard#247's turn-range
``TranscriptSegmentRecord`` slices — a batch over the per-record cap SPLITS into several
records rather than shrinking/emptying down to one. ``run()`` no-ops while
``ctx.config.transcripts_ship`` is ``False`` (D5). Wired into ``tick`` by :class:`TranscriptDrain`."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import (
    NormalizedTurn,
    SidechainConversation,
    ToolCall,
    TranscriptBatch,
    TranscriptPosition,
)
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.store.repository import TranscriptSegmentLedgerRow

_log = get_logger("blizzard.runner.loop")

#: The runner's own per-record cap (D4) — the well-behaved half of #247's two-sided
#: enforcement, deliberately below the hub's own 4 MB rogue-case `RECORD_MAX_BYTES`.
TRANSCRIPT_RECORD_MAX_BYTES = 1024 * 1024

#: The per-chunk budget (D4) — measured as the sum of `shipped_bytes` across the chunk's
#: segments, the only quantity the runner controls. Conservative, not identical, to what
#: the hub bills: `TranscriptIngestService._apply` charges only the turns payload
#: (`len(record.turns_json.encode("utf-8"))`, `hub/domain/transcripts.py`), never the
#: whole serialized record's `segment_id`/`chunk_id`/`node_id`/epoch/generation/turn-range
#: fields `shipped_bytes` also counts.
CHUNK_TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024

#: Backpressure cap on total unacked bytes across the WHOLE outbound buffer (F8), distinct
#: from `CHUNK_TRANSCRIPT_MAX_BYTES`'s per-chunk shipped total — self-clears as the drain catches up.
_MAX_BUFFERED_BYTES = 256 * 1024 * 1024

#: Bounds :meth:`TranscriptPump.pump_lease`'s pre-closure read — matches `TranscriptDrain`'s own.
PUMP_LEASE_MAX_SECONDS = 5.0

#: Safety valve on :meth:`TranscriptPump._drain_segment` (F2) — bounds a source that never
#: reports ``complete=True`` from looping forever if ``deadline`` is ever ``None``.
_PUMP_LEASE_MAX_ITERATIONS = 1000

#: Never-silent reasons (D4) — only `_CHUNK_BUDGET_EXCEEDED` latches `stop_transcript_segment_shipping`.
_RECORD_CAP_EXCEEDED = "record_cap_exceeded"
_RECORD_UNSHIPPABLE = "record_unshippable"
_CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"

#: This tick's own source read came back incomplete (`TranscriptBatch.truncated`/`.sidechain_truncated`).
_SOURCE_READ_TRUNCATED = "source_read_truncated"

#: F2: a lease-closure pump that could not fully catch up with the source before the shared
#: deadline — distinct from `_SOURCE_READ_TRUNCATED`, this tick's own incomplete read.
_LEASE_CLOSURE_INCOMPLETE = "lease_closure_incomplete"

#: Explicit worst-of ranking (F2), mildest first — see `mark_transcript_record_truncated`.
#: Public: `transcript_drain.py` extends it with its own `_HUB_CAPPED` reason.
TRUNCATION_REASON_SEVERITY: dict[str, int] = {
    _SOURCE_READ_TRUNCATED: 0,
    _RECORD_CAP_EXCEEDED: 1,
    _RECORD_UNSHIPPABLE: 2,
    _LEASE_CLOSURE_INCOMPLETE: 3,
}

#: `_pump_one`'s outcome (F2; F1 round 8's `stuck`) — see its own docstring for each value.
_PumpOutcome = Literal["caught_up", "incomplete", "not_attempted", "stuck"]
_CAUGHT_UP: _PumpOutcome = "caught_up"
_INCOMPLETE: _PumpOutcome = "incomplete"
_NOT_ATTEMPTED: _PumpOutcome = "not_attempted"
_STUCK: _PumpOutcome = "stuck"


@dataclass(frozen=True)
class TranscriptPump:
    """Advances every live segment one tick's worth forward — the lane's only producer
    of transcript records."""

    ctx: LoopContext

    def run(self, *, deadline: datetime | None = None) -> None:
        """Pump every open segment. ``deadline`` bounds only how many ADDITIONAL segments a
        run attempts once one is already in flight — never the duration of the one being
        read, which the harness source's own ``MAX_BATCH_BYTES`` window bounds instead, not
        wall-clock. ``TranscriptDrain.run`` passes only a FRACTION of its own budget (F9),
        reserving the rest for the flush."""
        if not self.ctx.config.transcripts_ship or self.ctx.transcripts is None:
            return
        for segment in self.ctx.store.open_transcript_segments():
            if deadline is not None and self.ctx.clock.now() >= deadline:
                break  # this run's bound reached — the rest catch up on a later tick
            self._pump_one_safe(segment)

    def pump_lease(self, lease_id: str, *, deadline: datetime | None = None) -> None:
        """Drain a closing lease's own still-open segment(s) before finalization excludes
        them from every later ``run()``. Unlike ``run()``, this DRAINS each segment — one
        ``_pump_one`` call only advances one read window, and no later tick is coming — so
        each is pumped until caught up or ``deadline`` (F2). A segment never even attempted
        before ``deadline`` is marked truncated too, same as a partially-drained one."""
        if not self.ctx.config.transcripts_ship or self.ctx.transcripts is None:
            return
        segments = [s for s in self.ctx.store.open_transcript_segments() if s.lease_id == lease_id]
        for i, segment in enumerate(segments):
            if deadline is not None and self.ctx.clock.now() >= deadline:
                # Every remaining segment loses just as silently as a partially-drained one.
                for remaining in segments[i:]:
                    self._mark_record_truncated(remaining, _LEASE_CLOSURE_INCOMPLETE)
                return
            self._drain_segment(segment.segment_id, deadline=deadline)

    def _drain_segment(self, segment_id: str, *, deadline: datetime | None) -> None:
        for _ in range(_PUMP_LEASE_MAX_ITERATIONS):
            segment = self.ctx.store.transcript_segment(segment_id)
            if segment is None:
                return  # the segment vanished from under us — nothing left to drain
            outcome = self._pump_one_safe(segment)
            if outcome == _CAUGHT_UP:
                return  # caught up — nothing more to gain from reading again right now
            if outcome in (_NOT_ATTEMPTED, _STUCK):
                # Retrying gains nothing for either outcome (F1 round 8) — mark and stop.
                self._mark_record_truncated(segment, _LEASE_CLOSURE_INCOMPLETE)
                return
            if deadline is not None and self.ctx.clock.now() >= deadline:
                self._mark_record_truncated(segment, _LEASE_CLOSURE_INCOMPLETE)
                return
        # The safety valve: a source that never reports `complete=True` across this many
        # reads is misbehaving — stop and mark, rather than spin forever.
        segment = self.ctx.store.transcript_segment(segment_id)
        if segment is not None:
            self._mark_record_truncated(segment, _LEASE_CLOSURE_INCOMPLETE)

    def _pump_one_safe(self, segment: TranscriptSegmentLedgerRow) -> _PumpOutcome:
        """review round 6 F2: one segment's own failure must not abort the loop. Returns
        ``_NOT_ATTEMPTED`` on a caught exception (verify round 8) — a raising segment must
        not spin ``pump_lease``'s drain loop, but at lease closure it must not read as
        caught-up either, or whatever the source held finalizes with no truncation trace."""
        try:
            return self._pump_one(segment)
        except Exception:
            _log.exception(
                "transcript pump: failed to pump segment — continuing with the rest",
                segment_id=segment.segment_id,
                session_id=segment.session_id,
            )
            return _NOT_ATTEMPTED

    def _pump_one(self, segment: TranscriptSegmentLedgerRow) -> _PumpOutcome:
        """Advance ``segment`` one read window forward (F2; F1 round 8). ``_NOT_ATTEMPTED``:
        nothing was read at all — ``pump_lease`` treats this as incomplete, not caught-up,
        since a finalizing segment gets no later tick to make up a read it never took.
        ``_STUCK``: read, but the cursor didn't move — same treatment; see that branch's
        own comment. Otherwise ``_CAUGHT_UP``/``_INCOMPLETE`` from ``batch.complete``."""
        if segment.shipping_stopped_reason is not None:
            return _CAUGHT_UP  # permanently stopped past the per-chunk budget (D4)
        budget_before = self.ctx.store.chunk_transcript_shipped_bytes(segment.chunk_id)
        if budget_before >= CHUNK_TRANSCRIPT_MAX_BYTES:
            self._stop_shipping(segment, _CHUNK_BUDGET_EXCEEDED)
            return _CAUGHT_UP
        if self.ctx.store.outstanding_transcript_buffer_bytes() >= _MAX_BUFFERED_BYTES:
            # F8: transient backpressure, not a latch — self-clears once the drain catches up.
            return _NOT_ATTEMPTED

        bindings = self.ctx.store.bindings_for_chunk(segment.chunk_id)
        spawn_cwd = SpawnCwd(self.ctx.config.workspace_root, bindings[0].workdir if bindings else None).path
        source = self.ctx.transcripts
        assert source is not None  # guarded in run()
        since = TranscriptPosition(segment.cursor) if segment.cursor is not None else None
        batch = source.turns_since(segment.session_id, spawn_cwd=spawn_cwd, since=since)
        if not batch.available:
            return _NOT_ATTEMPTED  # source unavailable this tick — retry from the same cursor next time
        if batch.truncated or batch.sidechain_truncated:
            self._mark_record_truncated(segment, _SOURCE_READ_TRUNCATED)
        new_cursor = batch.next_position.token if batch.next_position is not None else segment.cursor
        # Never silently dropped: a parent-out-of-window subagent surfaces here, latched
        # per (segment, agent_id) rather than re-warned every tick.
        newly_dropped_sidechains = [
            sc.agent_id
            for sc in batch.unlinked_sidechains
            if self.ctx.store.mark_sidechain_dropped_warned(segment.segment_id, agent_id=sc.agent_id)
        ]

        if not batch.turns:
            if new_cursor != segment.cursor:
                assert new_cursor is not None
                self.ctx.store.advance_transcript_cursor(
                    segment.segment_id,
                    cursor=new_cursor,
                    normalizer_version=batch.normalizer_version,
                    harness_version=batch.harness_version,
                )
            if newly_dropped_sidechains:
                self._warn_sidechains_dropped(segment, newly_dropped_sidechains)
            return _CAUGHT_UP if batch.complete else _INCOMPLETE

        # Turns present — the source must have advanced PAST them. An unchanged cursor
        # re-reads and re-ships the same turns every tick, under a fresh range each time.
        # A real conditional, not a bare `assert`: `python -O` strips assertions, which
        # would silently reopen the re-ship-forever defect this guards against (F3).
        if new_cursor is None or new_cursor == segment.cursor:
            _log.error(
                "transcript pump: turns present but cursor did not advance — skipping segment this tick",
                segment_id=segment.segment_id,
                session_id=segment.session_id,
            )
            # F3: latched above regardless — a dropped sidechain observed on a stuck-cursor
            # tick must still warn, or the latch means it never warns at all.
            if newly_dropped_sidechains:
                self._warn_sidechains_dropped(segment, newly_dropped_sidechains)
            # F1: genuine loss, not caught-up — `_CAUGHT_UP` here would finalize the
            # segment at lease closure with no truncation trace (see `_pump_one`'s own docstring).
            return _STUCK

        turn_range_start = segment.shipped_turns
        records, any_shrunk, any_unshippable = _build_records(segment, batch, turn_range_start)
        # The source's own tail-cap signal (D4) rides the LAST record this batch produced.
        if batch.truncated or batch.sidechain_truncated:
            records[-1]["record_truncated"] = True
        payloads = [json.dumps(record) for record in records]
        total_bytes = sum(len(p.encode("utf-8")) for p in payloads)

        if budget_before + total_bytes > CHUNK_TRANSCRIPT_MAX_BYTES:
            # All-or-nothing (F1): every record here advances the SAME cursor write, so
            # shipping only some would silently lose the rest's turns forever.
            self._stop_shipping(segment, _CHUNK_BUDGET_EXCEEDED)
            if newly_dropped_sidechains:  # this branch already read a real batch
                self._warn_sidechains_dropped(segment, newly_dropped_sidechains)
            return _CAUGHT_UP

        self.ctx.store.record_transcript_deltas(
            segment_id=segment.segment_id,
            chunk_id=segment.chunk_id,
            cursor=new_cursor,
            shipped_bytes=segment.shipped_bytes + total_bytes,
            shipped_turns=segment.shipped_turns + len(batch.turns),
            normalizer_version=batch.normalizer_version,
            harness_version=batch.harness_version,
            payloads=payloads,
            created_at=self.ctx.clock.now(),
        )
        # Order here no longer matters (F2, round 8): the store keeps the worse of the two
        # by the explicit severity each call carries, not by which call happened last.
        if any_shrunk:
            self._mark_record_truncated(segment, _RECORD_CAP_EXCEEDED)
        if any_unshippable:
            self._mark_record_truncated(segment, _RECORD_UNSHIPPABLE)
        if newly_dropped_sidechains:
            self._warn_sidechains_dropped(segment, newly_dropped_sidechains)
        return _CAUGHT_UP if batch.complete else _INCOMPLETE

    def _stop_shipping(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        changed = self.ctx.store.stop_transcript_segment_shipping(segment.segment_id, reason=reason)
        if changed:
            self._warn(segment, reason)

    def _mark_record_truncated(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        # Latched per (segment, reason) by the store (F2) — see its own docstring.
        changed = self.ctx.store.mark_transcript_record_truncated(
            segment.segment_id, reason=reason, severity=TRUNCATION_REASON_SEVERITY[reason]
        )
        if changed:
            self._warn(segment, reason)

    def _warn(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        OutboundFacts(self.ctx).transcript_truncated(
            chunk_id=segment.chunk_id, segment_id=segment.segment_id, reason=reason, at=self.ctx.clock.now()
        )

    def _warn_sidechains_dropped(self, segment: TranscriptSegmentLedgerRow, agent_ids: list[str | None]) -> None:
        # Latched per (segment, agent_id) via the store — fires only the first time this
        # segment warns about a given agent, never once per tick it recurs.
        OutboundFacts(self.ctx).event(
            chunk_id=segment.chunk_id,
            lease_id=None,
            at=self.ctx.clock.now(),
            payload={
                "severity": "warning",
                "kind": "transcript-sidechain-dropped",
                "chunk_id": segment.chunk_id,
                "lease_id": None,
                "node_name": None,
                "message": (
                    f"transcript segment {segment.segment_id} newly observed "
                    f"{len(agent_ids)} unlinked sidechain(s) (latched, not re-warned)"
                ),
                "detail": {"segment_id": segment.segment_id, "agent_ids": agent_ids},
            },
        )


def _turn_wire(turn: NormalizedTurn, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "kind": turn.kind,
        "timestamp": iso_utc(turn.timestamp) if turn.timestamp is not None else None,
        "text": turn.text,
        "tool": _tool_wire(turn.tool) if turn.tool is not None else None,
        "thinking_redacted": turn.thinking_redacted,
        "sidechain": _sidechain_wire(turn.sidechain) if turn.sidechain is not None else None,
        "truncated": turn.truncated,
    }


def _tool_wire(tool: ToolCall) -> dict[str, Any]:
    return {
        "name": tool.name,
        # F8: deep, not shallow — the shrink pass recurses into and mutates NESTED
        # containers too (e.g. `MultiEdit.edits`), which a shallow copy still shares.
        "input": copy.deepcopy(tool.input),
        "input_unparsed": tool.input_unparsed,
        "input_shape": tool.input_shape,
        "tool_use_id": tool.tool_use_id,
        "output": tool.output,
        "output_truncated": tool.output_truncated,
        # F5 (round 6): `ToolCall` carries no `input_truncated` — the harness seam never
        # observes it, only the shrink pass below (which mutates this wire dict in place)
        # ever sets it True.
        "input_truncated": False,
    }


def _sidechain_wire(sidechain: SidechainConversation) -> dict[str, Any]:
    # Sidechain turns carry no index of their own — #247's TurnSegmentView.index offsets the
    # *linked* stream, so these are addressed by position within `turns`.
    return {
        "agent_id": sidechain.agent_id,
        "agent_type": sidechain.agent_type,
        "link": sidechain.link,
        "turns": [_turn_wire(t, i) for i, t in enumerate(sidechain.turns)],
    }


def _record_envelope(
    segment: TranscriptSegmentLedgerRow, batch: TranscriptBatch, *, turn_range_start: int, turn_range_end: int
) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "chunk_id": segment.chunk_id,
        "node_id": segment.node_id,
        "epoch": segment.epoch,
        "spawn_generation": segment.generation,
        "turn_range_start": turn_range_start,
        "turn_range_end": turn_range_end,
        "final": False,
        "normalizer_version": batch.normalizer_version,
        "harness_version": batch.harness_version,
        "record_truncated": False,
        "turns": [],
    }


#: A group's fitting outcome (F1): `"ok"` as read, `"shrunk"` fit only after `_shrink_to_cap`,
#: `"unshippable"` stayed over cap even shrunk to nothing.
_GroupOutcome = Literal["ok", "shrunk", "unshippable"]


def _build_records(
    segment: TranscriptSegmentLedgerRow, batch: TranscriptBatch, turn_range_start: int
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Greedily pack ``batch.turns`` into ``TRANSCRIPT_RECORD_MAX_BYTES``-or-smaller records
    (F1), closing a group once the next turn's estimated cost would push it over cap; each
    closed group's REAL size is then verified (and shrunk/emptied if needed). A turn whose
    own cost alone exceeds the cap forms a group of one, scoping any fallback to it alone.
    Returns ``(records, any_shrunk, any_unshippable)`` for the caller's own worse-last mark."""
    wire_turns = [_turn_wire(t, turn_range_start + i) for i, t in enumerate(batch.turns)]
    turn_costs = [len(json.dumps(wt).encode("utf-8")) for wt in wire_turns]
    overhead = len(
        json.dumps(
            _record_envelope(segment, batch, turn_range_start=turn_range_start, turn_range_end=turn_range_start)
        ).encode("utf-8")
    )

    records: list[dict[str, Any]] = []
    outcomes: list[_GroupOutcome] = []

    def close_group(start: int, end: int) -> None:
        record = _record_envelope(
            segment, batch, turn_range_start=turn_range_start + start, turn_range_end=turn_range_start + end - 1
        )
        record["turns"] = wire_turns[start:end]
        payload = json.dumps(record)
        outcome: _GroupOutcome = "ok"
        if len(payload.encode("utf-8")) > TRANSCRIPT_RECORD_MAX_BYTES:
            record = _shrink_to_cap(record)
            record["record_truncated"] = True
            payload = json.dumps(record)
            outcome = "shrunk"
            if len(payload.encode("utf-8")) > TRANSCRIPT_RECORD_MAX_BYTES:
                # Every shrinkable field here is already empty — ship an empty slice over
                # just THIS group's claimed range, gapless, not the whole batch's (F1).
                record["turns"] = []
                record["record_truncated"] = True
                outcome = "unshippable"
        records.append(record)
        outcomes.append(outcome)

    # `json.dumps` separates array items with `", "` (2 bytes) — real for every turn but
    # the group's first, so the estimate must include it or many-small-turn groups undercount.
    _ARRAY_SEPARATOR_BYTES = 2
    group_start = 0
    running = overhead
    for i, cost in enumerate(turn_costs):
        separator = _ARRAY_SEPARATOR_BYTES if group_start < i else 0
        if group_start < i and running + separator + cost > TRANSCRIPT_RECORD_MAX_BYTES:
            close_group(group_start, i)
            group_start = i
            running = overhead
            separator = 0
        running += separator + cost
    close_group(group_start, len(wire_turns))

    return records, "shrunk" in outcomes, "unshippable" in outcomes


#: Safety valve on the proportional shrink below — real convergence takes 1-3 passes; this
#: only bounds pathological cases (heavy unicode escaping, many tiny fields) from looping.
_SHRINK_MAX_PASSES = 20

#: Margin applied to the target size a pass cuts down to, not the cut fraction.
_SHRINK_OVERCUT = 1.15
_SHRINK_TARGET_BYTES = int(TRANSCRIPT_RECORD_MAX_BYTES / _SHRINK_OVERCUT)


def _flat_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every turn in ``turns``, plus every turn nested under a sidechain — a sidechain
    turn's own text is exactly as shrinkable as its parent's."""
    flat: list[dict[str, Any]] = []
    for turn in turns:
        flat.append(turn)
        sidechain = turn.get("sidechain")
        if sidechain is not None:
            flat.extend(_flat_turns(sidechain["turns"]))
    return flat


#: A shrink candidate: ``(value_holder, field, marker_holder, marker_field)`` — the holder
#: is a ``dict`` or ``list``, both indexed the same way via ``holder[field]``.
_ShrinkCandidate = tuple[Any, Any, dict[str, Any], str]


def _walk_shrinkable_strings(
    container: object, marker_holder: dict[str, Any], marker_field: str, candidates: list[_ShrinkCandidate]
) -> None:
    """Recurse into nested ``dict``/``list`` containers, collecting every string leaf as a
    shrinkable candidate (F4) — e.g. ``MultiEdit.edits``, a list of ``{old_string,
    new_string}`` dicts, not a flat top-level string. Every leaf under one tool's ``input``
    shares that tool's one ``input_truncated`` marker."""
    items: list[tuple[Any, Any]]
    if isinstance(container, dict):
        items = list(container.items())
    elif isinstance(container, list):
        items = list(enumerate(container))
    else:
        return
    for key, value in items:
        if isinstance(value, str) and value:
            candidates.append((container, key, marker_holder, marker_field))
        elif isinstance(value, (dict, list)):
            _walk_shrinkable_strings(value, marker_holder, marker_field, candidates)


def _shrink_candidates(record: dict[str, Any]) -> list[_ShrinkCandidate]:
    """Every shrinkable ``(value_holder, field, marker_holder, marker_field)`` quadruple
    still carrying text: a turn's own ``text``, a tool call's ``output``, and every
    string leaf nested anywhere under its ``input`` (F4) or its own ``input_unparsed``.
    The marker location can differ from the value's: every leaf under one tool's ``input``
    shares that one tool-level ``input_truncated`` flag."""
    candidates: list[_ShrinkCandidate] = []
    for turn in _flat_turns(record["turns"]):
        if turn.get("text"):
            candidates.append((turn, "text", turn, "truncated"))
        tool = turn.get("tool")
        if tool is None:
            continue
        if tool.get("output"):
            candidates.append((tool, "output", tool, "output_truncated"))
        input_ = tool.get("input")
        if isinstance(input_, dict):
            _walk_shrinkable_strings(input_, tool, "input_truncated", candidates)
        if tool.get("input_unparsed"):
            candidates.append((tool, "input_unparsed", tool, "input_truncated"))
    return candidates


def _byte_cost(text: str) -> int:
    """A string's cost in the same unit ``_shrink_to_cap`` measures overshoot in:
    ``json.dumps``-escaped bytes, not raw Python ``len()``. ``json.dumps`` defaults to
    ``ensure_ascii=True``, so a non-ASCII character costs several encoded bytes (6 for a
    BMP character via ``\\uXXXX``, 12 for an astral one via a surrogate pair) but only 1
    Python ``len()`` unit — mixing the two units clamps ``keep_fraction`` to 0.0 on
    non-ASCII-heavy content (F1). The +2 for the surrounding quotes is a small, constant,
    shared overcount across every candidate — conservative, not a correctness issue."""
    return len(json.dumps(text))


def _shrink_to_cap(record: dict[str, Any]) -> dict[str, Any]:
    """Shrink turn text and tool-output fields in place — including nested sidechain turns —
    until the serialized record fits the per-record cap (D4). Never drops a turn: every
    shrinkable field is cut proportionally to its share of the overshoot, not just the single
    largest one, so a batch with many oversized fields converges in a few passes (a
    still-over-cap result is the caller's own :data:`_RECORD_UNSHIPPABLE`)."""
    for _ in range(_SHRINK_MAX_PASSES):
        size = len(json.dumps(record).encode("utf-8"))
        if size <= TRANSCRIPT_RECORD_MAX_BYTES:
            break
        candidates = _shrink_candidates(record)
        shrinkable = sum(_byte_cost(holder[field]) for holder, field, _, _ in candidates)
        if not candidates or shrinkable == 0:
            break  # nothing left to shrink; the cap stays exceeded by structure alone
        # A window many times over cap still keeps the fraction the budget allows, rather
        # than emptying every field in the first pass.
        overshoot = size - _SHRINK_TARGET_BYTES
        keep_fraction = max(0.0, (shrinkable - overshoot) / shrinkable)
        for holder, field, marker_holder, marker_field in candidates:
            text = holder[field]
            new_len = int(len(text) * keep_fraction)
            if new_len < len(text):
                holder[field] = text[:new_len]
                marker_holder[marker_field] = True
    return record
