"""The runner-plane, chunk-scoped transcript segment routes (runner-node-grouped-transcripts,
D1/D3) — a chunk's segment index and one segment's content by id, mirroring the hub's own
``/api/chunks/{chunk_id}/transcripts[/{segment_id}]`` path shape (D3, D5). Runner-local only:
both resolve through :class:`TranscriptService` (D4), never calling the hub; ownership is
structural — this runner's store only ever holds its own leases' segments. Distinct from
``transcripts.py``'s lease-keyed ``/api/leases/{lease_id}/transcript``, unchanged by this."""

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
    # No unavailability field on the wire view (D2) — `content.truncated`/`.turns` already
    # carry that mapping, minted once by `TranscriptService.segment_content`, never here too.
    return TranscriptSegmentContentView(
        segment_id=segment_id,
        final=content.final,
        truncated=content.truncated,
        turns=[turn_view(turn) for turn in content.turns],
    )


@router.get("/chunks/{chunk_id}/transcripts", response_model=TranscriptSegmentIndexView)
def list_transcript_segments(chunk_id: str, request: Request) -> TranscriptSegmentIndexView:
    """The chunk's segment index, straight off the local ledger — metadata and byte counts
    only, never turns (D6). A chunk this runner never held a lease for returns ``[]``."""
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
