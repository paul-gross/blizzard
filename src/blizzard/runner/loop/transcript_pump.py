"""The transcript lane's per-tick pump (issue #246) — advances each live segment's
forward-read cursor through ``IHarnessTranscriptSource.turns_since`` and enqueues its
record(s) atomically with the cursor write (D3), one-or-more of blizzard#247's turn-range
``TranscriptSegmentRecord`` slices — a batch over the per-record cap SPLITS into several
records rather than shrinking/emptying down to one. ``run()`` no-ops while
``ctx.config.transcripts_ship`` is ``False`` (D5). Wired into ``tick`` by :class:`TranscriptDrain`."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import (
    LateToolOutput,
    NormalizedTurn,
    SidechainConversation,
    ToolCall,
    TranscriptBatch,
    TranscriptPosition,
)
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.transcripts.caps import CHUNK_TRANSCRIPT_MAX_BYTES, TRANSCRIPT_RECORD_MAX_BYTES
from blizzard.runner.transcripts.ledger import TranscriptSegmentLedgerRow

_log = get_logger("blizzard.runner.loop")

#: Backpressure cap on total unacked bytes across the WHOLE outbound buffer, distinct
#: from `CHUNK_TRANSCRIPT_MAX_BYTES`'s per-chunk shipped total — self-clears as the drain catches up.
MAX_BUFFERED_BYTES = 256 * 1024 * 1024

#: Bounds :meth:`TranscriptPump.pump_lease`'s pre-closure read — matches `TranscriptDrain`'s own.
PUMP_LEASE_MAX_SECONDS = 5.0

#: Safety valve on :meth:`TranscriptPump.drain_segment` — bounds a source that never
#: reports ``complete=True`` from looping forever if ``deadline`` is ever ``None``.
_PUMP_LEASE_MAX_ITERATIONS = 1000

#: The sidechain link route this pump resolves (blizzard#338): the agent-id join, made
#: across a window boundary out of the segment's own persisted map rather than in-window.
LATE_AGENT_ID_LINK = "agent-id-late"

#: Never-silent reasons (D4) — only `_CHUNK_BUDGET_EXCEEDED` latches `stop_transcript_segment_shipping`.
_RECORD_CAP_EXCEEDED = "record_cap_exceeded"
_RECORD_UNSHIPPABLE = "record_unshippable"
_CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"

#: This tick's own source read came back incomplete (`TranscriptBatch.truncated`/`.sidechain_truncated`).
_SOURCE_READ_TRUNCATED = "source_read_truncated"

#: A lease-closure pump that could not fully catch up with the source before the shared
#: deadline — distinct from `_SOURCE_READ_TRUNCATED`, this tick's own incomplete read.
_LEASE_CLOSURE_INCOMPLETE = "lease_closure_incomplete"

#: The same loss on the backfill's own drain (blizzard#250) — named apart because no lease
#: closure is involved, ranked alike because the content is gone either way.
BACKFILL_INCOMPLETE = "backfill_incomplete"

#: Explicit worst-of ranking, mildest first — see `mark_transcript_record_truncated`.
#: Public: `transcript_drain.py` extends it with its own `HUB_CAPPED` reason.
TRUNCATION_REASON_SEVERITY: dict[str, int] = {
    _SOURCE_READ_TRUNCATED: 0,
    _RECORD_CAP_EXCEEDED: 1,
    _RECORD_UNSHIPPABLE: 2,
    _LEASE_CLOSURE_INCOMPLETE: 3,
    BACKFILL_INCOMPLETE: 3,
}

#: `_pump_one`'s outcome — see its own docstring for each value.
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

    @property
    def _record_max_bytes(self) -> int:
        """The configured per-record cap, or the module default (blizzard#338)."""
        configured = self.ctx.config.transcript_record_max_bytes
        return TRANSCRIPT_RECORD_MAX_BYTES if configured is None else configured

    @property
    def _chunk_max_bytes(self) -> int:
        """The configured per-chunk budget, or the module default (blizzard#338). Widened for a
        backfill window: a re-ship spends this budget a second time over the same chunk."""
        configured = self.ctx.config.transcript_chunk_max_bytes
        return CHUNK_TRANSCRIPT_MAX_BYTES if configured is None else configured

    def run(self, *, deadline: datetime | None = None) -> None:
        """Pump every open segment. ``deadline`` bounds only how many ADDITIONAL segments a
        run attempts once one is already in flight — never the duration of the one being
        read, which the harness source's own ``MAX_BATCH_BYTES`` window bounds instead, not
        wall-clock. ``TranscriptDrain.run`` passes only a FRACTION of its own budget,
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
        each is pumped until caught up or ``deadline``. A segment never even attempted
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
            self.drain_segment(segment.segment_id, deadline=deadline)

    def drain_segment(
        self, segment_id: str, *, deadline: datetime | None, incomplete_reason: str = _LEASE_CLOSURE_INCOMPLETE
    ) -> bool:
        """Read one segment forward until it is caught up, ``deadline`` passes, or reading
        again would gain nothing — marking ``incomplete_reason`` in the latter two cases.
        ``True`` iff the source was read to its end: a caller that closes the segment out
        must not do so on ``False``, or content it never read is sealed away."""
        for _ in range(_PUMP_LEASE_MAX_ITERATIONS):
            segment = self.ctx.store.transcript_segment(segment_id)
            if segment is None:
                return False  # the segment vanished from under us — nothing left to drain
            outcome = self._pump_one_safe(segment)
            if outcome == _CAUGHT_UP:
                return True  # caught up — nothing more to gain from reading again right now
            if outcome in (_NOT_ATTEMPTED, _STUCK):
                # Retrying gains nothing for either outcome — mark and stop.
                self._mark_record_truncated(segment, incomplete_reason)
                return False
            if deadline is not None and self.ctx.clock.now() >= deadline:
                self._mark_record_truncated(segment, incomplete_reason)
                return False
        # The safety valve: a source that never reports `complete=True` across this many
        # reads is misbehaving — stop and mark, rather than spin forever.
        segment = self.ctx.store.transcript_segment(segment_id)
        if segment is not None:
            self._mark_record_truncated(segment, incomplete_reason)
        return False

    def _pump_one_safe(self, segment: TranscriptSegmentLedgerRow) -> _PumpOutcome:
        """One segment's own failure must not abort the loop. Returns
        ``_NOT_ATTEMPTED`` on a caught exception — a raising segment must
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
        """Advance ``segment`` one read window forward. ``_NOT_ATTEMPTED``:
        nothing was read at all — ``pump_lease`` treats this as incomplete, not caught-up,
        since a finalizing segment gets no later tick to make up a read it never took.
        ``_STUCK``: read, but the cursor didn't move — same treatment; see that branch's
        own comment. Otherwise ``_CAUGHT_UP``/``_INCOMPLETE`` from ``batch.complete``."""
        if segment.shipping_stopped_reason is not None:
            return _CAUGHT_UP  # permanently stopped past the per-chunk budget (D4)
        chunk_max_bytes = self._chunk_max_bytes
        budget_before = self.ctx.store.chunk_transcript_shipped_bytes(segment.chunk_id)
        if budget_before >= chunk_max_bytes:
            self._stop_shipping(segment, _CHUNK_BUDGET_EXCEEDED)
            return _CAUGHT_UP
        if self.ctx.store.outstanding_transcript_buffer_bytes() >= MAX_BUFFERED_BYTES:
            # Transient backpressure, not a latch — self-clears once the drain catches up.
            return _NOT_ATTEMPTED

        bindings = self.ctx.store.bindings_for_chunk(segment.chunk_id)
        spawn_cwd = SpawnCwd(self.ctx.config.workspace_root, bindings[0].workdir if bindings else None).path
        source = self.ctx.transcripts
        if source is None:
            # A conditional, not an `assert`: `run`/`pump_lease` guard this, but `drain_segment`
            # is public, and `python -O` would strip the guard into an opaque `AttributeError`.
            return _NOT_ATTEMPTED
        since = TranscriptPosition(segment.cursor) if segment.cursor is not None else None
        batch = source.turns_since(segment.session_id, spawn_cwd=spawn_cwd, since=since)
        if not batch.available:
            return _NOT_ATTEMPTED  # source unavailable this tick — retry from the same cursor next time
        if batch.truncated or batch.sidechain_truncated:
            self._mark_record_truncated(segment, _SOURCE_READ_TRUNCATED)
        new_cursor = batch.next_position.token if batch.next_position is not None else segment.cursor
        # This segment's whole accumulated map, plus whatever this window just named — the
        # link the LATE branches below resolve against (blizzard#338).
        parents = {**segment.agent_tool_use_ids, **batch.agent_tool_use_ids}
        # Only a sidechain with no resolvable parent is still dropped. The latch is taken where
        # the warning is emitted, so a raise before it cannot latch an unwarned agent.
        dropped_sidechains = [sc.agent_id for sc in batch.unlinked_sidechains if _parent_of(sc, parents) is None]

        if not (batch.turns or batch.late_tool_outputs or _linkable(batch, parents)):
            if new_cursor != segment.cursor:
                assert new_cursor is not None
                self.ctx.store.advance_transcript_cursor(
                    segment.segment_id,
                    cursor=new_cursor,
                    normalizer_version=batch.normalizer_version,
                    harness_version=batch.harness_version,
                    agent_tool_use_ids=batch.agent_tool_use_ids,
                )
            if dropped_sidechains:
                self._warn_sidechains_dropped(segment, dropped_sidechains)
            return _CAUGHT_UP if batch.complete else _INCOMPLETE

        # Content present, so the cursor must advance past it or the same turns re-ship every
        # tick. A conditional, not an `assert`: `python -O` would strip the guard away.
        if new_cursor is None or new_cursor == segment.cursor:
            _log.error(
                "transcript pump: turns present but cursor did not advance — skipping segment this tick",
                segment_id=segment.segment_id,
                session_id=segment.session_id,
            )
            # A dropped sidechain observed on a stuck-cursor tick must still warn, or the
            # latch below means it never warns at all.
            if dropped_sidechains:
                self._warn_sidechains_dropped(segment, dropped_sidechains)
            # Genuine loss, not caught-up — `_CAUGHT_UP` here would finalize the
            # segment at lease closure with no truncation trace (see `_pump_one`'s own docstring).
            return _STUCK

        turn_range_start = segment.shipped_turns
        built = _build_records(
            segment, batch, turn_range_start, record_max_bytes=self._record_max_bytes, parents=parents
        )
        records, any_shrunk, any_unshippable = built.records, built.any_shrunk, built.any_unshippable
        # The source's own tail-cap signal (D4) rides the LAST record this batch produced.
        if batch.truncated or batch.sidechain_truncated:
            records[-1]["record_truncated"] = True
        payloads = [json.dumps(record) for record in records]
        total_bytes = sum(len(p.encode("utf-8")) for p in payloads)

        if budget_before + total_bytes > chunk_max_bytes:
            # All-or-nothing: every record here advances the SAME cursor write, so
            # shipping only some would silently lose the rest's turns forever.
            self._stop_shipping(segment, _CHUNK_BUDGET_EXCEEDED)
            if dropped_sidechains:  # this branch already read a real batch
                self._warn_sidechains_dropped(segment, dropped_sidechains)
            return _CAUGHT_UP

        self.ctx.store.record_transcript_deltas(
            segment_id=segment.segment_id,
            chunk_id=segment.chunk_id,
            cursor=new_cursor,
            shipped_bytes=segment.shipped_bytes + total_bytes,
            # The SYNTHESIZED turns count too, or the next window's `turn_range_start`
            # overlaps them and the hub's natural key rejects the re-used range.
            shipped_turns=segment.shipped_turns + built.turn_count,
            normalizer_version=batch.normalizer_version,
            harness_version=batch.harness_version,
            payloads=payloads,
            created_at=self.ctx.clock.now(),
            agent_tool_use_ids=batch.agent_tool_use_ids,
        )
        # Order here does not matter: the store keeps the worse of the two
        # by the explicit severity each call carries, not by which call happened last.
        if any_shrunk:
            self._mark_record_truncated(segment, _RECORD_CAP_EXCEEDED)
        if any_unshippable:
            self._mark_record_truncated(segment, _RECORD_UNSHIPPABLE)
        if dropped_sidechains:
            self._warn_sidechains_dropped(segment, dropped_sidechains)
        return _CAUGHT_UP if batch.complete else _INCOMPLETE

    def _stop_shipping(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        changed = self.ctx.store.stop_transcript_segment_shipping(segment.segment_id, reason=reason)
        if changed:
            self._warn(segment, reason)

    def _mark_record_truncated(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        # Latched per (segment, reason) by the store — see its own docstring.
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
        newly = [
            agent_id
            for agent_id in agent_ids
            if self.ctx.store.mark_sidechain_dropped_warned(segment.segment_id, agent_id=agent_id)
        ]
        if not newly:
            return
        agent_ids = newly
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
        # Deep, not shallow — the shrink pass recurses into and mutates NESTED
        # containers too (e.g. `MultiEdit.edits`), which a shallow copy still shares.
        "input": copy.deepcopy(tool.input),
        "input_unparsed": tool.input_unparsed,
        "input_shape": tool.input_shape,
        "tool_use_id": tool.tool_use_id,
        "output": tool.output,
        "output_truncated": tool.output_truncated,
        # Not a harness-seam field: only the shrink pass, mutating this dict, ever sets it True.
        "input_truncated": False,
    }


def _late_link_wires(batch: TranscriptBatch, start_index: int, parents: Mapping[str, str]) -> list[dict[str, Any]]:
    """The turns this window synthesizes for content whose own anchor shipped in an earlier
    one (blizzard#338): an output for a call already on the hub, and a subagent conversation
    whose spawning turn likewise. Both name that anchor by ``tool_use_id``, the only handle
    that survives a window boundary — and `lease_content_view`'s own index renumbering."""
    wires: list[dict[str, Any]] = []
    for late in batch.late_tool_outputs:
        wires.append(_output_patch_wire(late, start_index + len(wires)))
    for sidechain in batch.unlinked_sidechains:
        parent = _parent_of(sidechain, parents)
        if parent is None:
            continue  # genuinely unlinkable — the caller warns, and it stays dropped
        wires.append(_late_sidechain_wire(sidechain, start_index + len(wires), parent))
    return wires


def _output_patch_wire(late: LateToolOutput, index: int) -> dict[str, Any]:
    """A `tool` turn carrying ONLY an output, for the call named by ``tool_use_id``. A reader
    merges it onto that call; one that does not is left with a card it can recognize and skip,
    never a second call that looks like it really happened."""
    return {
        "index": index,
        "kind": "tool",
        "timestamp": None,
        "text": "",
        "tool": {
            "name": "",
            "input": {},
            "input_unparsed": None,
            "input_shape": "absent",
            "tool_use_id": late.tool_use_id,
            "output": late.output,
            "output_truncated": late.output_truncated,
            "input_truncated": False,
            "output_patch": True,
        },
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }


def _late_sidechain_wire(sidechain: SidechainConversation, index: int, parent_tool_use_id: str) -> dict[str, Any]:
    """A top-level `sidechain` turn for a conversation whose spawning turn already shipped —
    the wire's own shape for one carried apart from its parent, now naming that parent."""
    return {
        "index": index,
        "kind": "sidechain",
        "timestamp": None,
        "text": "",
        "tool": None,
        "thinking_redacted": False,
        # `link` names the route that ATTACHED it, and this one did attach — leaving
        # "unlinked" here would label a linked conversation as orphaned on every board.
        "sidechain": {
            **_sidechain_wire(sidechain),
            "link": LATE_AGENT_ID_LINK,
            "parent_tool_use_id": parent_tool_use_id,
        },
        "truncated": False,
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
        "supersedes": segment.supersedes,
        "turns": [],
    }


def _record_overhead(
    segment: TranscriptSegmentLedgerRow, batch: TranscriptBatch, *, turn_range_start: int, turn_count: int
) -> int:
    """The envelope's serialized cost with BOTH range fields at the widest value any group
    here can claim — they are decimal integers, and one measurement budgets every group, so
    widening only one still under-counts a later group's other field. Under-count those
    digits and a group packs to exactly the cap, lands over it, and is shrunk — for tiny
    turns with nothing shrinkable, emptied outright."""
    widest = turn_range_start + turn_count
    envelope = _record_envelope(segment, batch, turn_range_start=widest, turn_range_end=widest)
    return len(json.dumps(envelope).encode("utf-8"))


#: `json.dumps` separates array items with `", "` — real for every turn but a group's first.
_ARRAY_SEPARATOR_BYTES = 2

#: A group's fitting outcome: `"ok"` as read, `"shrunk"` fit only after `_shrink_to_cap`,
#: `"unshippable"` stayed over cap even shrunk to nothing.
_GroupOutcome = Literal["ok", "shrunk", "unshippable"]


def _parent_of(sidechain: SidechainConversation, parents: Mapping[str, str]) -> str | None:
    """The ``tool_use_id`` of the call that spawned ``sidechain``, or ``None`` when nothing
    ever named the pair — the one case still dropped and warned about (blizzard#338)."""
    return parents.get(sidechain.agent_id) if sidechain.agent_id is not None else None


def _linkable(batch: TranscriptBatch, parents: Mapping[str, str]) -> bool:
    return any(_parent_of(sc, parents) is not None for sc in batch.unlinked_sidechains)


@dataclass(frozen=True)
class _BuiltRecords:
    """:func:`_build_records`'s return. ``turn_count`` is the SHIPPED count — real turns plus
    the synthesized late-link ones — which is what the next window's range starts at."""

    records: list[dict[str, Any]]
    any_shrunk: bool
    any_unshippable: bool
    turn_count: int


def _build_records(
    segment: TranscriptSegmentLedgerRow,
    batch: TranscriptBatch,
    turn_range_start: int,
    *,
    record_max_bytes: int = TRANSCRIPT_RECORD_MAX_BYTES,
    parents: Mapping[str, str] | None = None,
) -> _BuiltRecords:
    """Greedily pack this window's turns into ``record_max_bytes``-or-smaller records,
    closing a group once the next turn's estimated cost would push it over cap; each
    closed group's REAL size is then verified (and shrunk/emptied if needed). A turn whose
    own cost alone exceeds the cap forms a group of one, scoping any fallback to it alone."""
    wire_turns = [_turn_wire(t, turn_range_start + i) for i, t in enumerate(batch.turns)]
    wire_turns += _late_link_wires(batch, turn_range_start + len(wire_turns), parents or {})
    turn_costs = [len(json.dumps(wt).encode("utf-8")) for wt in wire_turns]
    overhead = _record_overhead(segment, batch, turn_range_start=turn_range_start, turn_count=len(wire_turns))

    records: list[dict[str, Any]] = []
    outcomes: list[_GroupOutcome] = []

    def close_group(start: int, end: int) -> None:
        record = _record_envelope(
            segment, batch, turn_range_start=turn_range_start + start, turn_range_end=turn_range_start + end - 1
        )
        record["turns"] = wire_turns[start:end]
        payload = json.dumps(record)
        outcome: _GroupOutcome = "ok"
        if len(payload.encode("utf-8")) > record_max_bytes:
            record = _shrink_to_cap(record, record_max_bytes)
            record["record_truncated"] = True
            payload = json.dumps(record)
            outcome = "shrunk"
            if len(payload.encode("utf-8")) > record_max_bytes:
                # Every shrinkable field here is already empty — ship an empty slice over
                # just THIS group's claimed range, gapless, not the whole batch's.
                record["turns"] = []
                record["record_truncated"] = True
                outcome = "unshippable"
        records.append(record)
        outcomes.append(outcome)

    group_start = 0
    running = overhead
    for i, cost in enumerate(turn_costs):
        separator = _ARRAY_SEPARATOR_BYTES if group_start < i else 0
        if group_start < i and running + separator + cost > record_max_bytes:
            close_group(group_start, i)
            group_start = i
            running = overhead
            separator = 0
        running += separator + cost
    close_group(group_start, len(wire_turns))

    return _BuiltRecords(records, "shrunk" in outcomes, "unshippable" in outcomes, len(wire_turns))


#: Safety valve on the proportional shrink below — real convergence takes 1-3 passes; this
#: only bounds pathological cases (heavy unicode escaping, many tiny fields) from looping.
_SHRINK_MAX_PASSES = 20

#: Margin applied to the target size a pass cuts down to, not the cut fraction.
_SHRINK_OVERCUT = 1.15


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
    shrinkable candidate — e.g. ``MultiEdit.edits``, a list of ``{old_string,
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
    string leaf nested anywhere under its ``input`` or its own ``input_unparsed``.
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
    """A string's cost in the unit ``_shrink_to_cap`` measures overshoot in: ``json.dumps``-escaped
    bytes, not raw ``len()``. Under ``ensure_ascii=True`` a non-ASCII character costs several
    encoded bytes but one ``len()`` unit, and mixing the two clamps ``keep_fraction`` to 0.0.
    The +2 for the surrounding quotes overcounts every candidate equally."""
    return len(json.dumps(text))


def _shrink_to_cap(record: dict[str, Any], record_max_bytes: int = TRANSCRIPT_RECORD_MAX_BYTES) -> dict[str, Any]:
    """Shrink turn text and tool-output fields in place — including nested sidechain turns —
    until the serialized record fits the per-record cap (D4). Never drops a turn: every
    shrinkable field is cut proportionally to its share of the overshoot, not just the single
    largest one, so a batch with many oversized fields converges in a few passes (a
    still-over-cap result is the caller's own :data:`_RECORD_UNSHIPPABLE`)."""
    for _ in range(_SHRINK_MAX_PASSES):
        size = len(json.dumps(record).encode("utf-8"))
        if size <= record_max_bytes:
            break
        candidates = _shrink_candidates(record)
        shrinkable = sum(_byte_cost(holder[field]) for holder, field, _, _ in candidates)
        if not candidates or shrinkable == 0:
            break  # nothing left to shrink; the cap stays exceeded by structure alone
        # A window many times over cap still keeps the fraction the budget allows, rather
        # than emptying every field in the first pass.
        overshoot = size - int(record_max_bytes / _SHRINK_OVERCUT)
        keep_fraction = max(0.0, (shrinkable - overshoot) / shrinkable)
        for holder, field, marker_holder, marker_field in candidates:
            text = holder[field]
            new_len = int(len(text) * keep_fraction)
            if new_len < len(text):
                holder[field] = text[:new_len]
                marker_holder[marker_field] = True
    return record
