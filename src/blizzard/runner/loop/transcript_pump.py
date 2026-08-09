"""The transcript lane's per-tick pump (issue #246) — advances each live segment's
forward-read cursor through ``IHarnessTranscriptSource.turns_since`` and enqueues its
record atomically with the cursor write (D3), shaped as one of blizzard#247's turn-range
``TranscriptSegmentRecord`` slices. ``run()`` no-ops while ``ctx.config.transcripts_ship``
is ``False`` (D5); the lane around it does not — a segment still finalizes with a marker on
every closure regardless. Wired into ``tick`` by :class:`TranscriptDrain`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import NormalizedTurn, SidechainConversation, ToolCall, TranscriptPosition
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.store.repository import TranscriptSegmentLedgerRow

#: The runner's own per-record cap (D4) — the well-behaved half of #247's two-sided
#: enforcement, deliberately below the hub's own 4 MB rogue-case `RECORD_MAX_BYTES`.
TRANSCRIPT_RECORD_MAX_BYTES = 1024 * 1024

#: The per-chunk budget (D4) — measured as the sum of `shipped_bytes` across the chunk's
#: segments, the only quantity the runner controls and the hub bills against.
CHUNK_TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024

#: Bounds :meth:`TranscriptPump.pump_lease`'s pre-closure read (review F4) — matches
#: ``TranscriptDrain._MAX_SECONDS_PER_RUN``; D3's promise applies to closure too.
PUMP_LEASE_MAX_SECONDS = 5.0

#: Never-silent reasons (D4, review F1) — the first two are transient (``mark_transcript_
#: record_truncated``); only ``_CHUNK_BUDGET_EXCEEDED`` latches ``stop_transcript_segment_shipping``.
_RECORD_CAP_EXCEEDED = "record_cap_exceeded"
_RECORD_UNSHIPPABLE = "record_unshippable"
_CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"


@dataclass(frozen=True)
class TranscriptPump:
    """Advances every live segment one tick's worth forward — the lane's only producer
    of transcript records."""

    ctx: LoopContext

    def run(self, *, deadline: datetime | None = None) -> None:
        """Pump every open segment. ``deadline`` — the wall-clock instant (via the injected
        clock, not a raw monotonic read) this run must yield by — bounds this method's own
        unbounded-by-segment-count work the same way ``TranscriptDrain`` bounds its flush;
        ``TranscriptDrain.run`` computes one deadline and shares it across both halves."""
        if not self.ctx.config.transcripts_ship or self.ctx.transcripts is None:
            return
        for segment in self.ctx.store.open_transcript_segments():
            if deadline is not None and self.ctx.clock.now() >= deadline:
                break  # this run's bound reached — the rest catch up on a later tick
            self._pump_one(segment)

    def pump_lease(self, lease_id: str, *, deadline: datetime | None = None) -> None:
        """Read whatever a single lease's own still-open segment(s) have to ship, right
        before that lease closes — finalization excludes a segment from every later
        tick's ``run()``, so without this, content since the last pump would never be
        read. ``deadline`` bounds this call the same way ``run``'s does (review F4);
        ``Attempt.close`` also wraps it in its own exception isolation."""
        if not self.ctx.config.transcripts_ship or self.ctx.transcripts is None:
            return
        for segment in self.ctx.store.open_transcript_segments():
            if deadline is not None and self.ctx.clock.now() >= deadline:
                break  # this call's own bound reached — the rest catches up on a later tick
            if segment.lease_id == lease_id:
                self._pump_one(segment)

    def _pump_one(self, segment: TranscriptSegmentLedgerRow) -> None:
        if segment.shipping_stopped_reason is not None:
            return  # permanently stopped past the per-chunk budget (D4) — review F1
        budget_before = self.ctx.store.chunk_transcript_shipped_bytes(segment.chunk_id)
        if budget_before >= CHUNK_TRANSCRIPT_MAX_BYTES:
            self._stop_shipping(segment, _CHUNK_BUDGET_EXCEEDED)
            return

        bindings = self.ctx.store.bindings_for_chunk(segment.chunk_id)
        spawn_cwd = SpawnCwd(self.ctx.config.workspace_root, bindings[0].workdir if bindings else None).path
        source = self.ctx.transcripts
        assert source is not None  # guarded in run()
        since = TranscriptPosition(segment.cursor) if segment.cursor is not None else None
        batch = source.turns_since(segment.session_id, spawn_cwd=spawn_cwd, since=since)
        if not batch.available:
            return  # source unavailable this tick — retry from the same cursor next time
        new_cursor = batch.next_position.token if batch.next_position is not None else segment.cursor
        # A subagent conversation whose parent turn is outside the window surfaces here, not
        # in `batch.turns` — never silently dropped, so it always reaches a warning.
        dropped_sidechains = len(batch.unlinked_sidechains)

        if not batch.turns:
            if new_cursor != segment.cursor:
                assert new_cursor is not None
                self.ctx.store.advance_transcript_cursor(
                    segment.segment_id,
                    cursor=new_cursor,
                    normalizer_version=batch.normalizer_version,
                    harness_version=batch.harness_version,
                )
            if dropped_sidechains:
                self._warn_sidechains_dropped(segment, dropped_sidechains)
            return

        turn_range_start = segment.shipped_turns
        record: dict[str, Any] = {
            "segment_id": segment.segment_id,
            "chunk_id": segment.chunk_id,
            "node_id": segment.node_id,
            "epoch": segment.epoch,
            "spawn_generation": segment.generation,
            "turn_range_start": turn_range_start,
            "turn_range_end": turn_range_start + len(batch.turns) - 1,
            "final": False,
            "normalizer_version": batch.normalizer_version,
            "harness_version": batch.harness_version,
            "record_truncated": False,
            "turns": [_turn_wire(t, turn_range_start + i) for i, t in enumerate(batch.turns)],
        }
        payload = json.dumps(record)
        record_truncated = False
        if len(payload.encode("utf-8")) > TRANSCRIPT_RECORD_MAX_BYTES:
            record = _shrink_to_cap(record)
            payload = json.dumps(record)
            record_truncated = True

        if len(payload.encode("utf-8")) > TRANSCRIPT_RECORD_MAX_BYTES:
            # Structural overhead alone still exceeds the cap (review F2) — ship an empty
            # slice over the same claimed range, keeping it gapless, never an over-cap body.
            record["turns"] = []
            # `record_truncated` (review F5): this hub-accepted record's own loss, wire-visible
            # since the hub's own cap-rejection `truncated` signal never fires for it.
            record["record_truncated"] = True
            payload = json.dumps(record)
            self.ctx.store.record_transcript_delta(
                segment_id=segment.segment_id,
                chunk_id=segment.chunk_id,
                cursor=new_cursor,
                shipped_bytes=segment.shipped_bytes + len(payload.encode("utf-8")),
                shipped_turns=segment.shipped_turns + len(batch.turns),
                normalizer_version=batch.normalizer_version,
                harness_version=batch.harness_version,
                payload=payload,
                created_at=self.ctx.clock.now(),
            )
            self._mark_record_truncated(segment, _RECORD_UNSHIPPABLE)
            if dropped_sidechains:
                self._warn_sidechains_dropped(segment, dropped_sidechains)
            return

        delta_bytes = len(payload.encode("utf-8"))
        if budget_before + delta_bytes > CHUNK_TRANSCRIPT_MAX_BYTES:
            self._stop_shipping(segment, _CHUNK_BUDGET_EXCEEDED)
            # review F13: this branch already read a real batch — any dropped sidechain
            # must still warn, not vanish silently along with the tipping record.
            if dropped_sidechains:
                self._warn_sidechains_dropped(segment, dropped_sidechains)
            return

        self.ctx.store.record_transcript_delta(
            segment_id=segment.segment_id,
            chunk_id=segment.chunk_id,
            cursor=new_cursor,
            shipped_bytes=segment.shipped_bytes + delta_bytes,
            shipped_turns=segment.shipped_turns + len(batch.turns),
            normalizer_version=batch.normalizer_version,
            harness_version=batch.harness_version,
            payload=payload,
            created_at=self.ctx.clock.now(),
        )
        if record_truncated:
            self._mark_record_truncated(segment, _RECORD_CAP_EXCEEDED)
        if dropped_sidechains:
            self._warn_sidechains_dropped(segment, dropped_sidechains)

    def _stop_shipping(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        changed = self.ctx.store.stop_transcript_segment_shipping(segment.segment_id, reason=reason)
        if changed:
            self._warn(segment, reason)

    def _mark_record_truncated(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        # Gated on the store's per-reason guard (review F14): warns once per segment per
        # reason, not once per tick, but a later, worse reason still warns again.
        changed = self.ctx.store.mark_transcript_record_truncated(segment.segment_id, reason=reason)
        if changed:
            self._warn(segment, reason)

    def _warn(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        OutboundFacts(self.ctx).transcript_truncated(
            chunk_id=segment.chunk_id, segment_id=segment.segment_id, reason=reason, at=self.ctx.clock.now()
        )

    def _warn_sidechains_dropped(self, segment: TranscriptSegmentLedgerRow, count: int) -> None:
        # Never latching (unlike truncation, this claims no turn range on the wire lane at
        # all) — every occurrence is its own fact-lane warning, one per pump call that hits it.
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
                "message": f"transcript segment {segment.segment_id} dropped {count} unlinked sidechain(s)",
                "detail": {"segment_id": segment.segment_id, "count": count},
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
        "input": tool.input,
        "input_unparsed": tool.input_unparsed,
        "input_shape": tool.input_shape,
        "tool_use_id": tool.tool_use_id,
        "output": tool.output,
        "output_truncated": tool.output_truncated,
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


def _flat_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every turn in ``turns``, plus every turn nested under a sidechain (review F2) — a
    sidechain turn's own text is exactly as shrinkable as its parent's."""
    flat: list[dict[str, Any]] = []
    for turn in turns:
        flat.append(turn)
        sidechain = turn.get("sidechain")
        if sidechain is not None:
            flat.extend(_flat_turns(sidechain["turns"]))
    return flat


def _shrink_candidates(record: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Every shrinkable ``(holder, field)`` pair still carrying text: a turn's own ``text``
    (top-level or nested under a sidechain) and its tool call's ``output`` (review F2 — the
    ordinary case for a Claude Code transcript, where the oversized content is tool output,
    not the turn's own text)."""
    candidates: list[tuple[dict[str, Any], str]] = []
    for turn in _flat_turns(record["turns"]):
        if turn.get("text"):
            candidates.append((turn, "text"))
        tool = turn.get("tool")
        if tool is not None and tool.get("output"):
            candidates.append((tool, "output"))
    return candidates


#: Safety valve on the proportional shrink below — real convergence takes 1-3 passes; this
#: only bounds pathological cases (heavy unicode escaping, many tiny fields) from looping.
_SHRINK_MAX_PASSES = 20

#: Cut a bit more than the measured overshoot demands each pass, since JSON's per-field
#: overhead (quotes, commas, escaped unicode) makes byte count non-linear in text length.
_SHRINK_OVERCUT = 1.15


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
        shrinkable = sum(len(holder[field]) for holder, field in candidates)
        if not candidates or shrinkable == 0:
            break  # nothing left to shrink; the cap stays exceeded by structure alone
        overshoot = size - TRANSCRIPT_RECORD_MAX_BYTES
        keep_fraction = max(0.0, 1 - (overshoot / shrinkable) * _SHRINK_OVERCUT)
        for holder, field in candidates:
            text = holder[field]
            new_len = int(len(text) * keep_fraction)
            if new_len < len(text):
                holder[field] = text[:new_len]
                holder["output_truncated" if field == "output" else "truncated"] = True
    return record
