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

#: Bounds :meth:`TranscriptPump.pump_lease`'s pre-closure read — matches `TranscriptDrain`'s own.
PUMP_LEASE_MAX_SECONDS = 5.0

#: Never-silent reasons (D4) — only `_CHUNK_BUDGET_EXCEEDED` latches `stop_transcript_segment_shipping`.
_RECORD_CAP_EXCEEDED = "record_cap_exceeded"
_RECORD_UNSHIPPABLE = "record_unshippable"
_CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"

#: This tick's own source read came back incomplete (`TranscriptBatch.truncated`/`.sidechain_truncated`).
_SOURCE_READ_TRUNCATED = "source_read_truncated"


@dataclass(frozen=True)
class TranscriptPump:
    """Advances every live segment one tick's worth forward — the lane's only producer
    of transcript records."""

    ctx: LoopContext

    def run(self, *, deadline: datetime | None = None) -> None:
        """Pump every open segment. ``deadline`` bounds only how many ADDITIONAL segments a
        run attempts once one is already in flight — never the duration of the one being
        read, which the harness source's own ``MAX_BATCH_BYTES`` window bounds instead,
        not wall-clock. ``TranscriptDrain.run`` shares one deadline across both."""
        if not self.ctx.config.transcripts_ship or self.ctx.transcripts is None:
            return
        for segment in self.ctx.store.open_transcript_segments():
            if deadline is not None and self.ctx.clock.now() >= deadline:
                break  # this run's bound reached — the rest catch up on a later tick
            self._pump_one(segment)

    def pump_lease(self, lease_id: str, *, deadline: datetime | None = None) -> None:
        """Read whatever a single lease's own still-open segment(s) have to ship, right
        before that lease closes — finalization excludes a segment from every later tick's
        ``run()``, so without this, content since the last pump would never be read.
        ``deadline`` bounds this call the same way ``run``'s does."""
        if not self.ctx.config.transcripts_ship or self.ctx.transcripts is None:
            return
        for segment in self.ctx.store.open_transcript_segments():
            if deadline is not None and self.ctx.clock.now() >= deadline:
                break  # this call's own bound reached — the rest catches up on a later tick
            if segment.lease_id == lease_id:
                self._pump_one(segment)

    def _pump_one(self, segment: TranscriptSegmentLedgerRow) -> None:
        if segment.shipping_stopped_reason is not None:
            return  # permanently stopped past the per-chunk budget (D4)
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
            return

        # Turns present — the source must have advanced PAST them. An unchanged cursor
        # re-reads and re-ships the same turns every tick, under a fresh range each time.
        assert new_cursor is not None and new_cursor != segment.cursor
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
            "record_truncated": batch.truncated or batch.sidechain_truncated,
            "turns": [_turn_wire(t, turn_range_start + i) for i, t in enumerate(batch.turns)],
        }
        payload = json.dumps(record)
        record_truncated = False
        if len(payload.encode("utf-8")) > TRANSCRIPT_RECORD_MAX_BYTES:
            record = _shrink_to_cap(record)
            # Wire-visible even when shrinking alone closes the gap — the local flag below
            # only reaches the wire through the still-over-cap branch.
            record["record_truncated"] = True
            payload = json.dumps(record)
            record_truncated = True

        if len(payload.encode("utf-8")) > TRANSCRIPT_RECORD_MAX_BYTES:
            # Every shrinkable field is already empty — only per-turn JSON structure
            # remains, so ship an empty slice over the claimed range, gapless.
            record["turns"] = []
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
            if newly_dropped_sidechains:
                self._warn_sidechains_dropped(segment, newly_dropped_sidechains)
            return

        delta_bytes = len(payload.encode("utf-8"))
        if budget_before + delta_bytes > CHUNK_TRANSCRIPT_MAX_BYTES:
            self._stop_shipping(segment, _CHUNK_BUDGET_EXCEEDED)
            # This branch already read a real batch — any dropped sidechain still warns.
            if newly_dropped_sidechains:
                self._warn_sidechains_dropped(segment, newly_dropped_sidechains)
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
        if newly_dropped_sidechains:
            self._warn_sidechains_dropped(segment, newly_dropped_sidechains)

    def _stop_shipping(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        changed = self.ctx.store.stop_transcript_segment_shipping(segment.segment_id, reason=reason)
        if changed:
            self._warn(segment, reason)

    def _mark_record_truncated(self, segment: TranscriptSegmentLedgerRow, reason: str) -> None:
        # Gated on the store's per-reason guard: warns once per segment per reason.
        changed = self.ctx.store.mark_transcript_record_truncated(segment.segment_id, reason=reason)
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
        "input": dict(tool.input),  # copied — the shrink pass below mutates the wire dict in place
        "input_unparsed": tool.input_unparsed,
        "input_shape": tool.input_shape,
        "tool_use_id": tool.tool_use_id,
        "output": tool.output,
        "output_truncated": tool.output_truncated,
        "input_truncated": tool.input_truncated,
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
    """Every turn in ``turns``, plus every turn nested under a sidechain — a sidechain
    turn's own text is exactly as shrinkable as its parent's."""
    flat: list[dict[str, Any]] = []
    for turn in turns:
        flat.append(turn)
        sidechain = turn.get("sidechain")
        if sidechain is not None:
            flat.extend(_flat_turns(sidechain["turns"]))
    return flat


def _shrink_candidates(
    record: dict[str, Any],
) -> list[tuple[dict[str, Any], str, dict[str, Any], str]]:
    """Every shrinkable ``(value_holder, field, marker_holder, marker_field)`` quadruple
    still carrying text: a turn's own ``text``, a tool call's ``output``, and every
    string-valued key of its ``input``/``input_unparsed``. The marker location can differ
    from the value's: every input key shares one tool-level ``input_truncated`` flag."""
    candidates: list[tuple[dict[str, Any], str, dict[str, Any], str]] = []
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
            for key, value in input_.items():
                if isinstance(value, str) and value:
                    candidates.append((input_, key, tool, "input_truncated"))
        if tool.get("input_unparsed"):
            candidates.append((tool, "input_unparsed", tool, "input_truncated"))
    return candidates


#: Safety valve on the proportional shrink below — real convergence takes 1-3 passes; this
#: only bounds pathological cases (heavy unicode escaping, many tiny fields) from looping.
_SHRINK_MAX_PASSES = 20

#: Margin applied to the target size a pass cuts down to, not the cut fraction.
_SHRINK_OVERCUT = 1.15
_SHRINK_TARGET_BYTES = int(TRANSCRIPT_RECORD_MAX_BYTES / _SHRINK_OVERCUT)


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
        shrinkable = sum(len(holder[field]) for holder, field, _, _ in candidates)
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
