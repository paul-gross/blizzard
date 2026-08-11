"""Best-effort import of pre-lane worker transcripts (blizzard#250), plus the re-ship verb.

Walks the runner's own lease records — never the harness directory, which on a working
machine is mostly the operator's own sessions — opens a segment per session still on disk,
drains it through the ordinary pump, and closes it out **only once the source was read to
its end**, so a session this run could not finish stays open for the next one to resume."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.transcript_drain import HUB_CAPPED, TranscriptDrain
from blizzard.runner.loop.transcript_pump import BACKFILL_INCOMPLETE, MAX_BUFFERED_BYTES, TranscriptPump
from blizzard.runner.store.repository import TranscriptBackfillLease, TranscriptSegmentLedgerRow

_log = get_logger("blizzard.runner.loop")

#: One flush call's record bound — the lane's own drain uses the same, and this loops on it.
_FLUSH_BATCH = 50

#: A merged import claims the lease's first spawn: pre-epic sessions recorded no resume
#: offsets, so an in-place-resumed session has no seam to split a later generation at.
_MERGED_GENERATION = 1


class TranscriptReshipError(Exception):
    """A re-ship that cannot be attempted at all. A run that came back PARTIAL is not one
    of these — that outcome is the report's own ``complete``."""


@dataclass(frozen=True)
class TranscriptBackfillReport:
    """What one pass did, counted by session. Every count is local: ``imported`` means read
    and enqueued to the outbound lane, never that the hub accepted it — ``capped`` is the
    subset the hub refused or the runner stopped shipping."""

    imported: int
    already_present: int
    gone: int
    deferred: int
    capped: int


@dataclass(frozen=True)
class TranscriptReshipReport:
    """What one re-ship landed. ``segment_id`` is the NEW segment carrying the content;
    ``source_segment_id`` is untouched and stays on the board beside it. ``complete`` is the
    drain's read-to-the-end answer; the two reasons are whatever the new segment marked —
    ``shipping_stopped_reason`` is set when it shipped NOTHING despite completing."""

    source_segment_id: str
    segment_id: str
    session_id: str
    turns: int
    shipped_bytes: int
    complete: bool
    truncated_reason: str | None
    shipping_stopped_reason: str | None


@dataclass(frozen=True)
class TranscriptBackfill:
    """The ``blizzard runner transcript backfill`` verb's domain half."""

    ctx: LoopContext

    def run(self, *, dry_run: bool = False, limit: int | None = None) -> TranscriptBackfillReport:
        """Import every session-bearing lease's transcript still on disk and not already
        segmented, at most ``limit`` of them. ``dry_run`` classifies without opening,
        draining or shipping anything, so its counts are what a real run would attempt."""
        source = self.ctx.transcripts
        if source is None or not self.ctx.config.transcripts_ship:
            # The gate `TranscriptPump.run`/`pump_lease` hold too: the CLI's own refusal is
            # the operator's message, never the enforcement (`bzh:controller-read-only`).
            return TranscriptBackfillReport(imported=0, already_present=0, gone=0, deferred=0, capped=0)

        unfinished = self._unfinished()
        imported = already = gone = deferred = capped = 0
        seen = {segment.session_id for segment in unfinished}
        for segment in unfinished:
            if dry_run:
                imported += 1
            elif self._finish(segment.segment_id):
                imported += 1
                capped += self._was_capped(segment.segment_id)
            else:
                deferred += 1

        backpressured = False
        for lease in self.ctx.store.transcript_backfill_leases():
            if lease.session_id in seen:
                continue
            seen.add(lease.session_id)
            if lease.has_segment:
                already += 1
            elif source.size_bytes(lease.session_id, spawn_cwd=self._spawn_cwd(lease.chunk_id)) is None:
                # *Not readable by this run* — usually a rotated-away file, but a wrong
                # transcripts root or user reads alike. Nothing is written, so a rerun retries it.
                gone += 1
            elif limit is not None and imported >= limit:
                deferred += 1
            elif backpressured or self.ctx.store.outstanding_transcript_buffer_bytes() >= MAX_BUFFERED_BYTES:
                # Latched once tripped: nothing here shrinks the buffer again, since the
                # flush below is exactly what failed to.
                backpressured = True
                deferred += 1
            elif dry_run:
                imported += 1
            else:
                segment_id = self._open(lease)
                if self._finish(segment_id):
                    imported += 1
                    capped += self._was_capped(segment_id)
                else:
                    deferred += 1

        report = TranscriptBackfillReport(imported, already, gone, deferred, capped)
        _log.info(
            "transcript backfill complete",
            dry_run=dry_run,
            imported=imported,
            already_present=already,
            gone=gone,
            deferred=deferred,
            capped=capped,
        )
        return report

    def reship(self, source_segment_id: str) -> TranscriptReshipReport:
        """Re-read ``source_segment_id``'s session from the start under a NEW segment id.
        The duplicate is the mechanism: the hub's ingest is idempotent on ``(segment_id,
        turn_range_start)`` and returns early on an accepted record, so a segment that
        shipped short is never corrected in place — only superseded. The original is left
        as it shipped, so what the hub was once told stays on the record."""
        source = self.ctx.store.transcript_segment(source_segment_id)
        if source is None:
            raise TranscriptReshipError(f"no such transcript segment: {source_segment_id}")
        if self.ctx.transcripts is None or not self.ctx.config.transcripts_ship:
            # The same gate `run` holds — the CLI's refusal is the message, not the enforcement.
            raise TranscriptReshipError("[transcripts] ship is false — the lane is off")
        if self.ctx.store.active_lease(source.lease_id) is not None:
            # `run`'s own rule, for the same reason: a live lease's segment belongs to the
            # tick's pump. Re-shipping one races it, leaving two segments over one session.
            raise TranscriptReshipError(
                f"lease {source.lease_id} is still active — its segment belongs to the running "
                "pump; re-ship it once the lease closes"
            )
        if self.ctx.transcripts.size_bytes(source.session_id, spawn_cwd=self._spawn_cwd(source.chunk_id)) is None:
            # Nothing is written on this path, so a rerun retries once the root is right.
            raise TranscriptReshipError(
                f"session {source.session_id} is not readable by this runner — "
                "rotated away, or the transcripts root is not the one that wrote it"
            )

        # Resume an earlier re-ship this session left open rather than opening another: an
        # unconditional open strands the last one, which the board renders as still streaming.
        resumed = self._resumable(source)
        segment_id = resumed.segment_id if resumed is not None else self._open_beside(source)
        complete = self._finish(segment_id)
        # Read back: the pump owns every counter, and may have marked a truncation of its own.
        landed = self.ctx.store.transcript_segment(segment_id)
        _log.info(
            "transcript segment reshipped",
            source_segment_id=source_segment_id,
            segment_id=segment_id,
            session_id=source.session_id,
            resumed=resumed is not None,
            complete=complete,
        )
        return TranscriptReshipReport(
            source_segment_id=source_segment_id,
            segment_id=segment_id,
            session_id=source.session_id,
            turns=landed.shipped_turns if landed else 0,
            shipped_bytes=landed.shipped_bytes if landed else 0,
            complete=complete,
            truncated_reason=(landed.truncated_reason if landed else None) or None,
            shipping_stopped_reason=(landed.shipping_stopped_reason if landed else None) or None,
        )

    def _resumable(self, source: TranscriptSegmentLedgerRow) -> TranscriptSegmentLedgerRow | None:
        """An earlier re-ship's own still-open segment for this session, if one was left
        behind. Never ``source`` itself, which is finalized and stays as it shipped."""
        return next(
            (s for s in self._unfinished() if s.session_id == source.session_id and s.segment_id != source.segment_id),
            None,
        )

    def _open_beside(self, source: TranscriptSegmentLedgerRow) -> str:
        """A fresh segment over ``source``'s own lease coordinates, pointed at what it
        replaces. Without that pointer the hub's lease read — keyed on the lease, not the
        segment — concatenates both and renders the conversation twice."""
        return self.ctx.store.open_transcript_segment(
            chunk_id=source.chunk_id,
            node_id=source.node_id,
            epoch=source.epoch,
            generation=source.generation,
            lease_id=source.lease_id,
            session_id=source.session_id,
            stamped_at=self.ctx.clock.now(),
            supersedes=source.segment_id,
        )

    def _open(self, lease: TranscriptBackfillLease) -> str:
        return self.ctx.store.open_transcript_segment(
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            generation=_MERGED_GENERATION,
            lease_id=lease.lease_id,
            session_id=lease.session_id,
            stamped_at=self.ctx.clock.now(),
        )

    def _finish(self, segment_id: str) -> bool:
        """Drain one segment and close it out, shipping what it produced before the next
        session is read — without that flush a long run buries itself under the pump's own
        buffered-bytes backpressure. ``False`` leaves it open for a later run to resume."""
        # No deadline: an operator verb has all the time the file needs, unlike the tick's
        # shared budget, and the pump's own iteration valve still bounds a stuck source.
        caught_up = TranscriptPump(self.ctx).drain_segment(
            segment_id, deadline=None, incomplete_reason=BACKFILL_INCOMPLETE
        )
        if caught_up:
            self.ctx.store.finalize_transcript_segment(segment_id, finalized_at=self.ctx.clock.now())
        drain = TranscriptDrain(self.ctx)
        while drain.flush(limit=_FLUSH_BATCH, deadline=None) > 0:
            pass
        return caught_up

    def _was_capped(self, segment_id: str) -> int:
        """1 when the hub refused this segment's content or the runner stopped shipping it —
        an import the local counts alone would report as whole."""
        segment = self.ctx.store.transcript_segment(segment_id)
        if segment is None:
            return 0
        return int(segment.truncated_reason == HUB_CAPPED or segment.shipping_stopped_reason is not None)

    def _unfinished(self) -> list[TranscriptSegmentLedgerRow]:
        """Segments still open on an already-closed lease — an interrupted earlier run's
        own. A live lease's segment belongs to the tick's pump, never here."""
        return [
            segment
            for segment in self.ctx.store.open_transcript_segments()
            if self.ctx.store.active_lease(segment.lease_id) is None
        ]

    def _spawn_cwd(self, chunk_id: str) -> str | None:
        bindings = self.ctx.store.bindings_for_chunk(chunk_id)
        return SpawnCwd(self.ctx.config.workspace_root, bindings[0].workdir if bindings else None).path
