"""Draining the transcript lane's own outbound buffer (issue #246) — one fact at a time,
in order, until one will not deliver or this tick's own bound is reached. Structurally
apart from ``drain.py``'s ``OutboundDrain`` (D3): its own FIFO, its own hub call, its own
crash-point family — a transport failure here stops only this lane, never the fact lane's."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import HubClientError
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.transcript_pump import TranscriptPump
from blizzard.runner.store.repository import BufferedTranscriptDelta, TranscriptSegmentLedgerRow
from blizzard.wire.transcript_segment import TranscriptSegmentBatch, TranscriptSegmentRecord

_log = get_logger("blizzard.runner.loop")

# Submit -> ack. The after-submit.before-ack window is the lost-ack replay the hub's
# seq-idempotent high-water mark must absorb — its own family, never `flush.*`'s.
_CP_BEFORE_SUBMIT = crashpoint("transcript.before-submit", "fact at head of the transcript buffer; not submitted")
_CP_AFTER_SUBMIT = crashpoint("transcript.after-submit.before-ack", "hub applied the fact; ack not recorded")

#: The reason `_deliver` marks on a hub-cap-rejected record — distinct from `transcript_pump.py`'s own.
_HUB_CAPPED = "hub_capped"

#: Bounds this drain's own per-``run()`` work — a slow-but-healthy hub must still yield to
#: the next tick's fact-lane steps. Wall time via the injected clock, not a raw monotonic read.
_MAX_RECORDS_PER_RUN = 50
_MAX_SECONDS_PER_RUN = 5.0


@dataclass(frozen=True)
class TranscriptDrain:
    """The transcript lane's pump-then-flush — registered directly in ``tick`` (D3), never
    chained to ``Pull``'s own ``OutboundDrain``. Bounded per run, one deadline shared across
    the pump and the flush below; ships every closure's final marker regardless of
    ``[transcripts] ship`` (D4/D5)."""

    ctx: LoopContext

    def run(self) -> None:
        # Not last in `tick` — an uncaught raise must not skip a later step.
        try:
            self._run_unsafe()
        except Exception:
            _log.exception("transcript drain failed — continuing the tick", runner_id=self.ctx.config.runner_id)

    def _run_unsafe(self) -> None:
        deadline = self.ctx.clock.now() + timedelta(seconds=_MAX_SECONDS_PER_RUN)
        TranscriptPump(self.ctx).run(deadline=deadline)
        if self.ctx.clock.now() >= deadline:
            return  # the pump alone exhausted the shared bound; the flush catches up next tick
        # `limit` bounds the query itself, and is this run's ONLY count bound — a second,
        # loop-level guard would be dead code below this line's cap.
        pending = self.ctx.store.pending_transcript_outbound(limit=_MAX_RECORDS_PER_RUN)
        for delta in pending:
            if self.ctx.clock.now() >= deadline:
                break  # this run's wall-clock bound reached — retry the rest next tick
            if not self._deliver(delta):
                break  # transport failure — stop; retry the backlog next tick

    def _deliver(self, delta: BufferedTranscriptDelta) -> bool:
        record = self._render(delta)
        batch = TranscriptSegmentBatch(runner_id=self.ctx.config.runner_id, records=[record])
        _CP_BEFORE_SUBMIT.reached()
        try:
            ack = self.ctx.hub.push_transcripts(batch)
        except HubClientError:
            return False  # hub unreachable — stays buffered, retried next tick; the fact lane is unaffected
        _CP_AFTER_SUBMIT.reached()  # hub applied it; a crash here is the lost-ack replay
        if delta.seq in ack.capped:
            # A cap rejection is not idempotency — surface it, but do not wedge the FIFO
            # drain on a record the hub will never store in full: ack and move on (D6, D4).
            _log.error("hub capped buffered transcript record", seq=delta.seq, segment_id=delta.segment_id)
            # Never silent — the same segment-field/fact-lane pair the pump's own paths use.
            changed = self.ctx.store.mark_transcript_record_truncated(delta.segment_id, reason=_HUB_CAPPED)
            if changed:
                OutboundFacts(self.ctx).transcript_truncated(
                    chunk_id=delta.chunk_id, segment_id=delta.segment_id, reason=_HUB_CAPPED, at=self.ctx.clock.now()
                )
        self.ctx.store.ack_transcript_outbound(delta.seq, acked_at=self.ctx.clock.now())
        return True

    def _render(self, delta: BufferedTranscriptDelta) -> TranscriptSegmentRecord:
        """A non-final row's ``payload`` already IS the wire body, built by
        :class:`TranscriptPump`. A final marker's is deliberately minimal — every field it
        needs is already frozen on the ledger row, read straight from there."""
        if not delta.final:
            return TranscriptSegmentRecord.model_validate({"seq": delta.seq, **json.loads(delta.payload)})
        segment = self.ctx.store.transcript_segment(delta.segment_id)
        assert segment is not None  # a final marker's own segment row always exists (D1)
        return _final_record(delta.seq, segment)


def _final_record(seq: int, segment: TranscriptSegmentLedgerRow) -> TranscriptSegmentRecord:
    record_truncated = segment.truncated_reason is not None or segment.shipping_stopped_reason is not None
    return TranscriptSegmentRecord(
        seq=seq,
        segment_id=segment.segment_id,
        chunk_id=segment.chunk_id,
        node_id=segment.node_id,
        epoch=segment.epoch,
        spawn_generation=segment.generation,
        turn_range_start=segment.shipped_turns,
        turn_range_end=segment.shipped_turns - 1,  # empty range — a final marker claims no new turns
        final=True,
        normalizer_version=segment.normalizer_version,
        harness_version=segment.harness_version,
        record_truncated=record_truncated,
        turns=[],
    )
