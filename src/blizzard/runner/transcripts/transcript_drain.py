"""Draining the transcript lane's own outbound buffer — one or more records
per ``push_transcripts`` batch, in order, until one batch will not deliver or
this tick's own bound is reached. This lane has its own FIFO, hub call and crash-point
family, so a transport failure here stops only this lane."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.client import HubClientError, IHubClient, TranscriptPush
from blizzard.runner.hub.outbound import OutboundFacts
from blizzard.runner.transcripts.ledger import (
    BufferedTranscriptDelta,
    TranscriptSegmentState,
    TruncationReason,
)
from blizzard.runner.transcripts.transcript_pump import (
    TranscriptPump,
    TranscriptPumpConfig,
    TranscriptPumpContext,
    resolve_record_max_bytes,
)

_log = get_logger("blizzard.runner.loop")

# Submit -> ack. The after-submit.before-ack window is the lost-ack replay the hub's
# seq-idempotent high-water mark must absorb — its own family, never `flush.*`'s.
_CP_BEFORE_SUBMIT = crashpoint("transcript.before-submit", "fact at head of the transcript buffer; not submitted")
_CP_AFTER_SUBMIT = crashpoint("transcript.after-submit.before-ack", "hub applied the fact; ack not recorded")

#: A final marker's own upper bound, no render needed: its shape is a handful of short
#: ids and scalars with an empty `turns`, well under this even at the widest realistic id
#: lengths — a declared constant, not a measurement, is the honest fallback `_batches`
#: sizes it by; `_deliver_batch` alone ever renders one for real.
_FINAL_RECORD_SIZE_ESTIMATE_BYTES = 2048

#: A non-final delta's estimate padding, over `len(payload)` alone, for the `seq` field
#: the pushed record carries beside its body, which `payload` itself does not.
_SEQ_FIELD_ESTIMATE_BYTES = 24

#: Bounds this drain's own per-``run()`` work — checked only BETWEEN deliveries, so the
#: REAL worst case is this constant PLUS one in-flight delivery's own push timeout.
_MAX_RECORDS_PER_RUN = 50
_MAX_SECONDS_PER_RUN = 5.0

#: The pump alone must not be able to consume the WHOLE run budget —
#: reserves the other half for the flush below, so it cannot starve indefinitely.
_PUMP_BUDGET_FRACTION = 0.5


class TranscriptDrainConfig(TranscriptPumpConfig, Protocol):
    @property
    def runner_name(self) -> str: ...


class TranscriptDrainContext(TranscriptPumpContext, Protocol):
    @property
    def config(self) -> TranscriptDrainConfig: ...
    @property
    def hub(self) -> IHubClient: ...


@dataclass(frozen=True)
class TranscriptDrain:
    """The transcript lane's pump-then-flush — registered directly in ``tick``, never
    chained to ``Pull``'s own ``OutboundDrain``. Bounded per run, one shared budget split
    between the pump and the flush below (the pump alone cannot starve the flush);
    ships every closure's final marker regardless of ``[transcripts] ship``."""

    ctx: TranscriptDrainContext

    def run(self) -> None:
        # Not last in `tick` — an uncaught raise must not skip a later step.
        try:
            self._run_unsafe()
        except Exception:
            _log.exception("transcript drain failed — continuing the tick", runner_name=self.ctx.config.runner_name)

    def _run_unsafe(self) -> None:
        started = self.ctx.clock.now()
        deadline = started + timedelta(seconds=_MAX_SECONDS_PER_RUN)
        pump_deadline = started + timedelta(seconds=_MAX_SECONDS_PER_RUN * _PUMP_BUDGET_FRACTION)
        # Defense in depth: `TranscriptPump.run` isolates each segment's own failure, but one
        # raised outside that loop — `open_transcript_segments()` itself — must not skip the flush.
        try:
            TranscriptPump(self.ctx).run(deadline=pump_deadline)
        except Exception:
            _log.exception(
                "transcript pump failed — the buffered flush below still runs", runner_name=self.ctx.config.runner_name
            )
        if self.ctx.clock.now() >= deadline:
            return  # the pump alone exhausted the shared bound; the flush catches up next tick
        self.flush(limit=_MAX_RECORDS_PER_RUN, deadline=deadline)

    def flush(self, *, limit: int, deadline: datetime | None) -> int:
        """Deliver buffered records FIFO, batched under the configured per-record byte cap
        (one oversized record ships alone), until ``limit``, ``deadline``, or the first
        batch that will not deliver, returning how many landed. Checked once per batch,
        not per record."""
        # `limit` bounds the query itself, and is this call's ONLY count bound — a second,
        # loop-level guard would be dead code below this line's cap.
        pending = self.ctx.stores.transcript_ledger.pending_transcript_outbound(limit=limit)
        delivered = 0
        for batch in self._batches(pending):
            if deadline is not None and self.ctx.clock.now() >= deadline:
                break  # this run's wall-clock bound reached — retry the rest next tick
            if not self._deliver_batch(batch):
                break  # transport failure — stop; retry the backlog next tick
            delivered += len(batch)
        return delivered

    def _batches(self, pending: list[BufferedTranscriptDelta]) -> Iterator[list[BufferedTranscriptDelta]]:
        """Greedily group pending deltas into batches at or below the per-record byte cap
        (one oversized record still ships alone), sized by :meth:`_estimated_size`."""
        cap = self._record_max_bytes
        batch: list[BufferedTranscriptDelta] = []
        batch_bytes = 0
        for delta in pending:
            record_bytes = self._estimated_size(delta)
            if batch and batch_bytes + record_bytes > cap:
                yield batch
                batch = []
                batch_bytes = 0
            batch.append(delta)
            batch_bytes += record_bytes
        if batch:
            yield batch

    @staticmethod
    def _estimated_size(delta: BufferedTranscriptDelta) -> int:
        """A delta's estimated wire size, always ``>=`` its actual rendered length: a
        non-final delta's ``payload`` already IS the wire body sans ``seq``, with every
        optional field the wire model can default already written explicit rather than
        omitted, so its own byte length plus a fixed pad for that field is exact enough to
        bound; a final marker's payload is not the wire body at all, so it sizes by the
        declared upper bound instead."""
        if delta.final:
            return _FINAL_RECORD_SIZE_ESTIMATE_BYTES
        return len(delta.payload.encode("utf-8")) + _SEQ_FIELD_ESTIMATE_BYTES

    @property
    def _record_max_bytes(self) -> int:
        return resolve_record_max_bytes(self.ctx)

    def _deliver_batch(self, deltas: list[BufferedTranscriptDelta]) -> bool:
        # The one render per record — `_batches` above never renders, only estimates.
        final_segments = self.ctx.stores.transcript_ledger.transcript_segments(
            [delta.segment_id for delta in deltas if delta.final]
        )
        records = [self._render(delta, final_segments) for delta in deltas]
        _CP_BEFORE_SUBMIT.reached()
        try:
            ack = self.ctx.hub.push_transcripts(records)
        except HubClientError:
            return False  # hub unreachable — the batch stays buffered, retried next tick; the fact lane is unaffected
        _CP_AFTER_SUBMIT.reached()  # hub applied it; a crash here is the lost-ack replay
        refused = set(ack.refused)
        for delta in deltas:
            if delta.seq in refused:
                # The hub owns this record's (chunk, epoch) elsewhere — it stores none of it and moves its
                # high-water past it. Acked so the drain never wedges, but never silently.
                _log.warning(
                    "hub refused buffered transcript record — its epoch is not this runner's",
                    seq=delta.seq,
                    segment_id=delta.segment_id,
                    chunk_id=delta.chunk_id,
                )
                self._mark_truncated(delta, TruncationReason.HUB_REFUSED)
            if delta.seq in ack.capped:
                # A cap rejection is not idempotency — surface it, but do not wedge the FIFO
                # drain on a record the hub will never store in full: ack and move on.
                _log.error("hub capped buffered transcript record", seq=delta.seq, segment_id=delta.segment_id)
                self._mark_truncated(delta, TruncationReason.HUB_CAPPED)
        seqs = [delta.seq for delta in deltas]
        self.ctx.stores.transcript_ledger.ack_transcript_outbound_batch(seqs, acked_at=self.ctx.clock.now())
        return True

    def _mark_truncated(self, delta: BufferedTranscriptDelta, reason: TruncationReason) -> None:
        """Never silent — the segment-field/fact-lane pair the pump's own paths use; the fact
        goes out only when the mark changed the segment's displayed reason."""
        changed = self.ctx.stores.transcript_ledger.mark_transcript_record_truncated(
            delta.segment_id, reason=reason, severity=reason.severity
        )
        if changed:
            OutboundFacts(self.ctx).transcript_truncated(
                chunk_id=delta.chunk_id, segment_id=delta.segment_id, reason=reason, at=self.ctx.clock.now()
            )

    def _render(
        self, delta: BufferedTranscriptDelta, final_segments: dict[str, TranscriptSegmentState]
    ) -> TranscriptPush:
        """A non-final row's ``payload`` already IS the record's body, built by
        :class:`TranscriptPump`. A final marker's is deliberately minimal — every field it
        needs is already frozen on the ledger row, read from ``final_segments`` — one
        :meth:`~IReadTranscriptLedgerRepository.transcript_segments` call per batch, rather
        than one :meth:`~IReadTranscriptLedgerRepository.transcript_segment` per final
        marker — so it reflects an earlier batch's just-applied hub-cap ack."""
        if not delta.final:
            return TranscriptPush(seq=delta.seq, body=_scrub_surrogates(json.loads(delta.payload)))
        segment = final_segments.get(delta.segment_id)
        if segment is None:
            # A final marker's own segment row always exists; a conditional rather than
            # an `assert`, which `python -O` strips into an opaque `AttributeError` below.
            raise RuntimeError(f"final transcript marker {delta.seq} has no segment row {delta.segment_id}")
        return _final_record(delta.seq, segment)


def _scrub_surrogates(value: Any) -> Any:
    """U+FFFD for each lone surrogate a mid-pair preview cut leaves, which no UTF-8 body can carry."""
    if isinstance(value, str):
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, list):
        return [_scrub_surrogates(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_surrogates(item) for key, item in value.items()}
    return value


def _final_record(seq: int, segment: TranscriptSegmentState) -> TranscriptPush:
    return TranscriptPush(
        seq=seq,
        body={
            "segment_id": segment.segment_id,
            "chunk_id": segment.chunk_id,
            "node_id": segment.node_id,
            "epoch": segment.epoch,
            "spawn_generation": segment.generation,
            "turn_range_start": segment.shipped_turns,
            "turn_range_end": segment.shipped_turns - 1,  # empty range — a final marker claims no new turns
            "final": True,
            "harness_id": segment.harness_id,
            "normalizer_version": segment.normalizer_version,
            "harness_version": segment.harness_version,
            "model": segment.model,
            "effort": segment.effort,
            "spawn_cwd": segment.spawn_cwd,
            "record_truncated": segment.truncated,
            "supersedes": segment.supersedes,
            "turns": [],
        },
    )
