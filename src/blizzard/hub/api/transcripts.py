"""Transcript-segment routes (blizzard#247) — the operator-plane discovery/content reads,
plus the wire<->domain rendering the fleet router's ingest route shares.

The discovery route (D12) returns segment metadata and byte counts only — never turn
content — so a caller must hold a ``segment_id`` from it before the content route
answers anything. Gated on :data:`~blizzard.auth_core.TRANSCRIPT_READ` (D11)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import TRANSCRIPT_READ
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.transcripts import (
    SegmentIndexRow,
    SegmentRecord,
    SegmentRecordContent,
    TranscriptIngestResult,
)
from blizzard.wire.transcript_segment import (
    TranscriptSegmentAck,
    TranscriptSegmentContentView,
    TranscriptSegmentIndexEntry,
    TranscriptSegmentIndexView,
    TranscriptSegmentRecord,
    TurnSegmentView,
)

router = APIRouter(prefix="/api", tags=["transcripts"], dependencies=[Depends(reject_runner_principal)])


# --- wire <-> domain rendering, shared with the fleet router's ingest route -----


def to_domain_record(record: TranscriptSegmentRecord, *, runner_id: str) -> SegmentRecord:
    """The wire ingest record, store-shaped — turns serialized once here (D4's byte
    count is measured off this same JSON text) rather than re-serialized per read."""
    turns_json = json.dumps([turn.model_dump(mode="json") for turn in record.turns])
    return SegmentRecord(
        segment_id=record.segment_id,
        chunk_id=record.chunk_id,
        node_id=record.node_id,
        epoch=record.epoch,
        spawn_generation=record.spawn_generation,
        runner_id=runner_id,
        turn_range_start=record.turn_range_start,
        turn_range_end=record.turn_range_end,
        final=record.final,
        normalizer_version=record.normalizer_version,
        harness_version=record.harness_version,
        record_truncated=record.record_truncated,
        turns_json=turns_json,
    )


def to_ack(runner_id: str, result: TranscriptIngestResult) -> TranscriptSegmentAck:
    return TranscriptSegmentAck(
        runner_id=runner_id,
        high_water=result.high_water,
        applied=result.applied,
        already_applied=result.already_applied,
        capped=result.capped,
    )


def _index_entry(row: SegmentIndexRow) -> TranscriptSegmentIndexEntry:
    return TranscriptSegmentIndexEntry(
        segment_id=row.segment_id,
        node_id=row.node_id,
        epoch=row.epoch,
        spawn_generation=row.spawn_generation,
        turn_range_start=row.turn_range_start,
        turn_range_end=row.turn_range_end,
        final=row.final,
        truncated=row.truncated,
        byte_count=row.byte_count,
        normalizer_version=row.normalizer_version,
        harness_version=row.harness_version,
        received_at=iso_utc(row.received_at),
    )


def _content_view(segment_id: str, records: list[SegmentRecordContent]) -> TranscriptSegmentContentView:
    turns: list[TurnSegmentView] = []
    for record in records:
        if record.rejected:
            continue
        turns.extend(TurnSegmentView.model_validate(turn) for turn in json.loads(record.turns_json))
    return TranscriptSegmentContentView(
        segment_id=segment_id,
        final=any(record.final for record in records),
        # A cap rejection (this hub's own) OR a runner-declared `record_truncated` — an
        # accepted record the runner itself had to ship turns-empty.
        truncated=any(record.rejected or record.record_truncated for record in records),
        turns=turns,
    )


# --- operator-plane routes ------------------------------------------------------


@router.get(
    "/chunks/{chunk_id}/transcripts",
    response_model=TranscriptSegmentIndexView,
    dependencies=[Depends(require(TRANSCRIPT_READ))],
)
def list_transcript_segments(
    chunk_id: str, services: Annotated[HubServices, Depends(get_services)]
) -> TranscriptSegmentIndexView:
    """The chunk's segment index (D12) — metadata and byte counts only, never turns."""
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    rows = services.transcripts.segments_for_chunk(chunk_id)
    return TranscriptSegmentIndexView(chunk_id=chunk_id, segments=[_index_entry(row) for row in rows])


@router.get(
    "/chunks/{chunk_id}/transcripts/{segment_id}",
    response_model=TranscriptSegmentContentView,
    dependencies=[Depends(require(TRANSCRIPT_READ))],
)
def get_transcript_segment(
    chunk_id: str, segment_id: str, services: Annotated[HubServices, Depends(get_services)]
) -> TranscriptSegmentContentView:
    """One segment's decompressed turns, concatenated across its stored records in
    turn-range order — the lazy per-segment content read (D12)."""
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    records = services.transcripts.records_for_segment(chunk_id, segment_id)
    if not records:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown segment {segment_id}")
    return _content_view(segment_id, records)
