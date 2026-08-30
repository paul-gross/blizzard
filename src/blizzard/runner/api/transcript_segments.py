"""The runner-plane, chunk-scoped transcript segment routes (runner-node-grouped-
transcripts, D1/D3) — a chunk's segment index and one segment's content by id, mirroring
the hub's own ``/api/chunks/{chunk_id}/transcripts[/{segment_id}]`` path shape so the
generated runner SDK exposes the same operation names (D3, D5).

Runner-local only: both routes resolve through :class:`TranscriptService`
(D4), which never calls the hub for these two reads. Ownership needs no explicit guard —
this runner's store only ever holds its own leases' segments, so a chunk it never held
returns an empty index, and a segment id from a different runner resolves as not-found
(D3). Distinct from ``transcripts.py``'s lease-keyed ``/api/leases/{lease_id}/transcript``,
which stays exactly as it is."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.transcript_rendering import turn_view
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.store.repository import TranscriptSegmentLedgerRow
from blizzard.runner.transcripts.service import ResolvedSegmentContent
from blizzard.wire.transcript_segment import (
    TranscriptSegmentContentView,
    TranscriptSegmentIndexEntry,
    TranscriptSegmentIndexView,
)

router = APIRouter(prefix="/api", tags=["runner"])


def _index_entry(row: TranscriptSegmentLedgerRow) -> TranscriptSegmentIndexEntry:
    # Segment-relative, gapless indexing restarts at 0 for every segment (issue #246):
    # `shipped_turns` is this segment's own running count, so it doubles as `turn_range_end`.
    return TranscriptSegmentIndexEntry(
        segment_id=row.segment_id,
        node_id=row.node_id,
        epoch=row.epoch,
        spawn_generation=row.generation,
        turn_range_start=0,
        turn_range_end=row.shipped_turns,
        final=row.finalized_at is not None,
        truncated=row.truncated_reason is not None,
        byte_count=row.shipped_bytes,
        normalizer_version=row.normalizer_version,
        harness_version=row.harness_version,
        received_at=iso_utc(row.stamped_at),
    )


def _content_view(segment_id: str, content: ResolvedSegmentContent) -> TranscriptSegmentContentView:
    # `TranscriptSegmentContentView` carries no dedicated unavailability field (D2) — an
    # unavailable session file renders as `truncated=True` over an empty `turns`, never a
    # silent empty transcript and never a 404 (D1).
    if not content.available:
        return TranscriptSegmentContentView(segment_id=segment_id, final=content.final, truncated=True, turns=[])
    return TranscriptSegmentContentView(
        segment_id=segment_id,
        final=content.final,
        truncated=content.truncated,
        turns=[turn_view(turn) for turn in content.turns],
    )


@router.get("/chunks/{chunk_id}/transcripts", response_model=TranscriptSegmentIndexView)
def list_transcript_segments(chunk_id: str, request: Request) -> TranscriptSegmentIndexView:
    """The chunk's segment index, straight off the local ledger — metadata and byte counts
    only, never turns (D12). A chunk this runner never held a lease for returns ``[]``."""
    service = RunnerWiring.of(request).transcripts()
    segments = service.segments_for_chunk(chunk_id)
    return TranscriptSegmentIndexView(chunk_id=chunk_id, segments=[_index_entry(row) for row in segments])


@router.get("/chunks/{chunk_id}/transcripts/{segment_id}", response_model=TranscriptSegmentContentView)
def get_transcript_segment(chunk_id: str, segment_id: str, request: Request) -> TranscriptSegmentContentView:
    """One segment's turns, read from its session file, local-only (D1) — 404 iff no such
    segment exists under this chunk on this runner's own store."""
    service = RunnerWiring.of(request).transcripts()
    content = service.segment_content(chunk_id, segment_id)
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown segment {segment_id}")
    return _content_view(segment_id, content)
