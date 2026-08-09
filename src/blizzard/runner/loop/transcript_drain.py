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
from blizzard.runner.loop.transcript_pump import TranscriptPump
from blizzard.runner.store.repository import BufferedTranscriptDelta
from blizzard.wire.transcript_outbound import TranscriptFact, TranscriptFactBatch

_log = get_logger("blizzard.runner.loop")

# Submit -> ack. The after-submit.before-ack window is the lost-ack replay the hub's
# seq-idempotent high-water mark must absorb — its own family, never `flush.*`'s.
_CP_BEFORE_SUBMIT = crashpoint("transcript.before-submit", "fact at head of the transcript buffer; not submitted")
_CP_AFTER_SUBMIT = crashpoint("transcript.after-submit.before-ack", "hub applied the fact; ack not recorded")

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
        deadline = self.ctx.clock.now() + timedelta(seconds=_MAX_SECONDS_PER_RUN)
        TranscriptPump(self.ctx).run(deadline=deadline)
        # `limit` bounds the query itself — else a large backlog's full payload set (up to
        # 1 MB/record) loads every tick regardless of how few records the loop below lets through.
        pending = self.ctx.store.pending_transcript_outbound(limit=_MAX_RECORDS_PER_RUN)
        for delivered, delta in enumerate(pending):
            if delivered >= _MAX_RECORDS_PER_RUN or self.ctx.clock.now() >= deadline:
                break  # this run's bound reached — retry the rest next tick
            if not self._deliver(delta):
                break  # transport failure — stop; retry the backlog next tick

    def _deliver(self, delta: BufferedTranscriptDelta) -> bool:
        batch = TranscriptFactBatch(
            runner_id=self.ctx.config.runner_id,
            facts=[TranscriptFact(seq=delta.seq, kind=delta.kind, payload=json.loads(delta.payload))],
        )
        _CP_BEFORE_SUBMIT.reached()
        try:
            ack = self.ctx.hub.push_transcripts(batch)
        except HubClientError:
            return False  # hub unreachable — stays buffered, retried next tick; the fact lane is unaffected
        _CP_AFTER_SUBMIT.reached()  # hub applied it; a crash here is the lost-ack replay
        if delta.seq in ack.rejected:
            # A contract rejection is not idempotency — surface it, but do not wedge the FIFO
            # drain on a fact the hub will never accept: ack and move on.
            _log.error("hub rejected buffered transcript fact", seq=delta.seq, kind=delta.kind)
        self.ctx.store.ack_transcript_outbound(delta.seq, acked_at=self.ctx.clock.now())
        return True
