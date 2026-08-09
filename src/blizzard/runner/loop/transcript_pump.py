"""The transcript lane's per-tick pump (issue #246) — advances each live segment's
forward-read cursor through ``IHarnessTranscriptSource.turns_since`` and enqueues its
delta atomically with the cursor write (D3). ``run()`` no-ops while
``ctx.config.transcripts_ship`` is ``False`` (D5); the lane around it does not — a segment
still stamps and finalizes with a marker on every closure regardless of ``ship``. Wired into
``tick`` by :class:`~blizzard.runner.loop.transcript_drain.TranscriptDrain`."""

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
from blizzard.runner.store.repository import TranscriptSegmentRecord
from blizzard.wire.transcript_outbound import TRANSCRIPT_RECORD_MAX_BYTES

#: The per-chunk budget (D4) — measured as the sum of `shipped_bytes` across the chunk's
#: segments, the only quantity the runner controls and the hub bills against.
CHUNK_TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024

#: Never-silent reasons (D4, review F1) — the first two are transient (``mark_transcript_
#: record_truncated``); only ``_CHUNK_BUDGET_EXCEEDED`` latches ``stop_transcript_segment_shipping``.
_RECORD_CAP_EXCEEDED = "record_cap_exceeded"
_RECORD_UNSHIPPABLE = "record_unshippable"
_CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"


@dataclass(frozen=True)
class TranscriptPump:
    """Advances every live segment one tick's worth forward — the lane's only producer
    of transcript deltas."""

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

    def pump_lease(self, lease_id: str) -> None:
        """Read whatever a single lease's own still-open segment(s) have to ship, right
        before that lease closes. ``Attempt.close`` calls this ahead of ``record_closure``:
        finalization excludes a segment from every later tick's ``run()``, so without this,
        content written between the previous tick's pump and this closure — often the
        worker's last output before failing — would never be read at all."""
        if not self.ctx.config.transcripts_ship or self.ctx.transcripts is None:
            return
        for segment in self.ctx.store.open_transcript_segments():
            if segment.lease_id == lease_id:
                self._pump_one(segment)

    def _pump_one(self, segment: TranscriptSegmentRecord) -> None:
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
        # in `batch.turns` — never silently dropped, so its count rides whatever ships next.
        dropped_sidechains = len(batch.unlinked_sidechains)

        if not batch.turns:
            if dropped_sidechains == 0:
                # A window can move the read position with no turn produced (e.g. control
                # records the normalizer drops) — persist the advance, or every later tick re-reads it.
                if new_cursor != segment.cursor:
                    assert new_cursor is not None
                    self.ctx.store.advance_transcript_cursor(segment.segment_id, cursor=new_cursor)
                return
            # No turn landed, but an unlinked sidechain did — record the loss explicitly
            # rather than advancing the cursor with no trace of it at all.
            payload = json.dumps(
                {"segment_id": segment.segment_id, "turns": [], "unlinked_sidechains_dropped": dropped_sidechains}
            )
            self.ctx.store.record_transcript_delta(
                segment_id=segment.segment_id,
                chunk_id=segment.chunk_id,
                cursor=new_cursor,
                shipped_bytes=segment.shipped_bytes + len(payload.encode("utf-8")),
                shipped_turns=segment.shipped_turns,
                payload=payload,
                created_at=self.ctx.clock.now(),
            )
            return

        delta: dict[str, Any] = {"segment_id": segment.segment_id, "turns": [_turn_wire(t) for t in batch.turns]}
        if dropped_sidechains:
            delta["unlinked_sidechains_dropped"] = dropped_sidechains
        payload = json.dumps(delta)
        record_truncated = False
        if len(payload.encode("utf-8")) > TRANSCRIPT_RECORD_MAX_BYTES:
            delta = _shrink_to_cap(delta)
            payload = json.dumps(delta)
            record_truncated = True

        if len(payload.encode("utf-8")) > TRANSCRIPT_RECORD_MAX_BYTES:
            # Structural overhead alone still exceeds the cap (review F2) — ship a small
            # marker instead of an over-cap body the hub would reject-but-ack anyway.
            unshippable: dict[str, Any] = {
                "segment_id": segment.segment_id,
                "turns": [],
                "turns_dropped": len(batch.turns),
            }
            if dropped_sidechains:
                unshippable["unlinked_sidechains_dropped"] = dropped_sidechains
            payload = json.dumps(unshippable)
            self.ctx.store.record_transcript_delta(
                segment_id=segment.segment_id,
                chunk_id=segment.chunk_id,
                cursor=new_cursor,
                shipped_bytes=segment.shipped_bytes + len(payload.encode("utf-8")),
                shipped_turns=segment.shipped_turns,
                payload=payload,
                created_at=self.ctx.clock.now(),
            )
            self._mark_record_truncated(segment, _RECORD_UNSHIPPABLE)
            return

        delta_bytes = len(payload.encode("utf-8"))
        if budget_before + delta_bytes > CHUNK_TRANSCRIPT_MAX_BYTES:
            self._stop_shipping(segment, _CHUNK_BUDGET_EXCEEDED)
            return

        self.ctx.store.record_transcript_delta(
            segment_id=segment.segment_id,
            chunk_id=segment.chunk_id,
            cursor=new_cursor,
            shipped_bytes=segment.shipped_bytes + delta_bytes,
            shipped_turns=segment.shipped_turns + len(batch.turns),
            payload=payload,
            created_at=self.ctx.clock.now(),
        )
        if record_truncated:
            self._mark_record_truncated(segment, _RECORD_CAP_EXCEEDED)

    def _stop_shipping(self, segment: TranscriptSegmentRecord, reason: str) -> None:
        changed = self.ctx.store.stop_transcript_segment_shipping(segment.segment_id, reason=reason)
        if changed:
            self._warn(segment, reason)

    def _mark_record_truncated(self, segment: TranscriptSegmentRecord, reason: str) -> None:
        # `_RECORD_CAP_EXCEEDED` never latches, so this can be reached every tick — gate on
        # the store's first-occurrence guard so the fact lane warns once per segment, not once per tick.
        changed = self.ctx.store.mark_transcript_record_truncated(segment.segment_id, reason=reason)
        if changed:
            self._warn(segment, reason)

    def _warn(self, segment: TranscriptSegmentRecord, reason: str) -> None:
        OutboundFacts(self.ctx).transcript_truncated(
            chunk_id=segment.chunk_id, segment_id=segment.segment_id, reason=reason, at=self.ctx.clock.now()
        )


def _turn_wire(turn: NormalizedTurn) -> dict[str, Any]:
    return {
        "index": turn.index,
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
    return {
        "agent_id": sidechain.agent_id,
        "agent_type": sidechain.agent_type,
        "link": sidechain.link,
        "turns": [_turn_wire(t) for t in sidechain.turns],
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


def _shrink_candidates(delta: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Every shrinkable ``(holder, field)`` pair still carrying text: a turn's own ``text``
    (top-level or nested under a sidechain) and its tool call's ``output`` (review F2 — the
    ordinary case for a Claude Code transcript, where the oversized content is tool output,
    not the turn's own text)."""
    candidates: list[tuple[dict[str, Any], str]] = []
    for turn in _flat_turns(delta["turns"]):
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


def _shrink_to_cap(delta: dict[str, Any]) -> dict[str, Any]:
    """Shrink turn text and tool-output fields in place — including nested sidechain turns —
    until the serialized delta fits the per-record cap (D4). Never drops a turn: every
    shrinkable field is cut proportionally to its share of the overshoot, not just the single
    largest one, so a batch with many oversized fields converges in a few passes (a
    still-over-cap result is the caller's own :data:`_RECORD_UNSHIPPABLE`)."""
    for _ in range(_SHRINK_MAX_PASSES):
        size = len(json.dumps(delta).encode("utf-8"))
        if size <= TRANSCRIPT_RECORD_MAX_BYTES:
            break
        candidates = _shrink_candidates(delta)
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
    return delta
