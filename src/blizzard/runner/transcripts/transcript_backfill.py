"""Best-effort import of pre-lane worker transcripts, plus the re-ship verb.

Walks the runner's own lease records — never the harness directory, which on a working
machine is mostly the operator's own sessions — opens a segment per session still on disk,
drains it through the ordinary pump, and closes it out **only once the source was read to
its end**, so a session this run could not finish stays open for the next one to resume."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.leases import IReadLeaseRecordRepository
from blizzard.runner.transcripts.backfill import (
    SegmentOpening,
    TranscriptReshipError,
    classify_backfill,
    newest_superseder,
    require_reshippable,
    resumable_for,
    unfinished,
)
from blizzard.runner.transcripts.caps import CHUNK_TRANSCRIPT_MAX_BYTES
from blizzard.runner.transcripts.ledger import TranscriptSegmentState, TruncationReason
from blizzard.runner.transcripts.transcript_drain import TranscriptDrain, TranscriptDrainContext
from blizzard.runner.transcripts.transcript_pump import (
    MAX_BUFFERED_BYTES,
    TranscriptPump,
    TranscriptPumpStores,
)

_log = get_logger("blizzard.runner.loop")

#: One flush call's record bound — the lane's own drain uses the same, and this loops on it.
_FLUSH_BATCH = 50

__all__ = [
    "TranscriptBackfill",
    "TranscriptBackfillContext",
    "TranscriptBackfillReport",
    "TranscriptBackfillStores",
    "TranscriptReshipError",
    "TranscriptReshipReport",
]


@domain_model
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


@domain_model
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


class TranscriptBackfillStores(TranscriptPumpStores, Protocol):
    @property
    def lease_record(self) -> IReadLeaseRecordRepository: ...


class TranscriptBackfillContext(TranscriptDrainContext, Protocol):
    @property
    def stores(self) -> TranscriptBackfillStores: ...


@dataclass(frozen=True)
class TranscriptBackfill:
    """The ``blizzard runner transcript backfill`` verb's domain half."""

    ctx: TranscriptBackfillContext

    def run(self, *, dry_run: bool = False, limit: int | None = None) -> TranscriptBackfillReport:
        """Import every session-bearing lease's transcript still on disk and not already
        segmented, at most ``limit`` of them. ``dry_run`` classifies without opening,
        draining or shipping anything, so its counts are what a real run would attempt."""
        if not self.ctx.transcripts_wired or not self.ctx.config.transcripts_ship:
            # The gate `TranscriptPump.run`/`pump_lease` hold too: the CLI's own refusal is
            # the operator's message, never the enforcement (`bzh:controller-read-only`).
            return TranscriptBackfillReport(imported=0, already_present=0, gone=0, deferred=0, capped=0)

        unfinished = self._unfinished()
        imported = already = gone = deferred = 0
        imported_segment_ids: list[str] = []
        seen = {segment.session for segment in unfinished}
        for segment in unfinished:
            if dry_run:
                imported += 1
            elif self._finish(segment.segment_id):
                imported += 1
                imported_segment_ids.append(segment.segment_id)
            else:
                deferred += 1

        backpressured = False
        for lease in self.ctx.stores.transcript_ledger.transcript_backfill_leases():
            if lease.session in seen:
                continue
            seen.add(lease.session)
            readable = not lease.has_segment and not self._unreadable(lease.session, chunk_id=lease.chunk_id)
            # Latched once tripped: nothing here shrinks the buffer again, since the flush
            # below is exactly what failed to.
            backpressured = backpressured or (
                readable
                and (limit is None or imported < limit)
                and self.ctx.stores.transcript_ledger.outstanding_transcript_buffer_bytes() >= MAX_BUFFERED_BYTES
            )
            verdict = classify_backfill(
                lease, readable=readable, imported=imported, limit=limit, backpressured=backpressured
            )
            if verdict == "already_present":
                already += 1
            elif verdict == "gone":
                gone += 1
            elif verdict == "deferred":
                deferred += 1
            elif dry_run:
                imported += 1
            else:
                segment_id = self._open(SegmentOpening.merged_import(lease, spawn_cwd=self._spawn_cwd(lease.chunk_id)))
                if self._finish(segment_id):
                    imported += 1
                    imported_segment_ids.append(segment_id)
                else:
                    deferred += 1

        capped = self._capped_count(imported_segment_ids)
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
        source = self.ctx.stores.transcript_ledger.transcript_segment(source_segment_id)
        if source is None:
            raise TranscriptReshipError(f"no such transcript segment: {source_segment_id}")
        if not self.ctx.transcripts_wired or not self.ctx.config.transcripts_ship:
            # The same gate `run` holds — the CLI's refusal is the message, not the enforcement.
            raise TranscriptReshipError("[transcripts] ship is false — the lane is off")
        chunk_segments = self.ctx.stores.transcript_ledger.transcript_segments_for_chunk(source.chunk_id)
        shipped = self.ctx.stores.transcript_ledger.chunk_transcript_shipped_bytes([source.chunk_id])
        require_reshippable(
            source,
            lease_active=self.ctx.stores.lease_record.active_lease(source.lease_id) is not None,
            chunk_shipped_bytes=shipped.get(source.chunk_id, 0),
            chunk_max_bytes=self._chunk_max_bytes(),
        )
        # An already-superseded source reships its chain's newest finalized superseder.
        target = newest_superseder(source, chunk_segments)
        try:
            readable = self.ctx.transcript_source_for(source.session)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            raise TranscriptReshipError(
                f"session {source.session_id}'s recorded owner {source.harness_id!r} is not "
                f"resolvable by this runner right now: {exc}"
            ) from exc
        if readable.size_bytes(source.session_id, spawn_cwd=self._spawn_cwd(source.chunk_id)) is None:
            # Nothing is written on this path, so a rerun retries once the root is right.
            raise TranscriptReshipError(
                f"session {source.session_id} is not readable by this runner — "
                "rotated away, or the transcripts root is not the one that wrote it"
            )

        # Resume an earlier re-ship this session left open rather than opening another: an
        # unconditional open strands the last one, which the board renders as still streaming.
        resumed = resumable_for(target, self._unfinished())
        segment_id = resumed.segment_id if resumed is not None else self._open(SegmentOpening.superseding(target))
        complete = self._finish(segment_id)
        # Read back: the pump owns every counter, and may have marked a truncation of its own.
        landed = self.ctx.stores.transcript_ledger.transcript_segment(segment_id)
        _log.info(
            "transcript segment reshipped",
            source_segment_id=source_segment_id,
            superseded_segment_id=target.segment_id,
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

    def _open(self, opening: SegmentOpening) -> str:
        return self.ctx.stores.transcript_ledger.open_transcript_segment(
            chunk_id=opening.chunk_id,
            node_id=opening.node_id,
            epoch=opening.epoch,
            generation=opening.generation,
            lease_id=opening.lease_id,
            session=opening.session,
            stamped_at=self.ctx.clock.now(),
            supersedes=opening.supersedes,
            spawn_cwd=opening.spawn_cwd,
        )

    def _chunk_max_bytes(self) -> int:
        configured = self.ctx.config.transcript_chunk_max_bytes
        return CHUNK_TRANSCRIPT_MAX_BYTES if configured is None else configured

    def _finish(self, segment_id: str) -> bool:
        """Drain one segment and close it out, shipping what it produced before the next
        session is read — without that flush a long run buries itself under the pump's own
        buffered-bytes backpressure. ``False`` leaves it open for a later run to resume."""
        # No deadline: an operator verb has all the time the file needs, unlike the tick's
        # shared budget, and the pump's own iteration valve still bounds a stuck source.
        caught_up = TranscriptPump(self.ctx).drain_segment(
            segment_id, deadline=None, incomplete_reason=TruncationReason.BACKFILL_INCOMPLETE
        )
        if caught_up:
            self.ctx.stores.transcript_ledger.finalize_transcript_segment(segment_id, finalized_at=self.ctx.clock.now())
        drain = TranscriptDrain(self.ctx)
        while drain.flush(limit=_FLUSH_BATCH, deadline=None) > 0:
            pass
        return caught_up

    def _capped_count(self, segment_ids: list[str]) -> int:
        """How many of ``segment_ids`` the hub refused, or the runner stopped shipping —
        one bulk read after the import loops, rather than one
        :meth:`~IReadTranscriptLedgerRepository.transcript_segment` per imported segment."""
        if not segment_ids:
            return 0
        segments = self.ctx.stores.transcript_ledger.transcript_segments(segment_ids)
        return sum(1 for segment in segments.values() if segment.lost_to_cap)

    def _unfinished(self) -> list[TranscriptSegmentState]:
        """Segments still open on an already-closed lease — an interrupted earlier run's
        own. A live lease's segment belongs to the tick's pump, never here."""
        active_lease_ids = {lease.lease_id for lease in self.ctx.stores.lease_record.list_active_leases()}
        return unfinished(self.ctx.stores.transcript_ledger.open_transcript_segments(), active_lease_ids)

    def _spawn_cwd(self, chunk_id: str) -> str | None:
        bindings = self.ctx.stores.environments.bindings_for_chunk(chunk_id)
        return SpawnCwd(self.ctx.config.workspace_root, bindings[0].workdir if bindings else None).path

    def _unreadable(self, session: SessionReference, *, chunk_id: str) -> bool:
        """True when ``session``'s transcript cannot be read right now — a rotated-away
        file, a wrong transcripts root, or a recorded owner this run's registry cannot
        resolve. Never raises, so one such session is skipped rather than aborting the run."""
        try:
            source = self.ctx.transcript_source_for(session)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            _log.warning(
                "transcript backfill skipped a session — recorded owner unresolvable",
                session_id=session.session_id,
                harness_id=session.harness_id,
                detail=str(exc),
            )
            return True
        return source.size_bytes(session.session_id, spawn_cwd=self._spawn_cwd(chunk_id)) is None
