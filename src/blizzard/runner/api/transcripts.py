"""The transcript read — ``GET /api/leases/{lease_id}/transcript`` (issue #29, blizzard#249).

**Lease-keyed, not session-keyed**: ``session_id`` is nullable, so a session-keyed route
could only 404 a ``spawning`` lease, collapsing "agent is starting up" into "not found".
**200-always with an in-band ``reason``**, never a 5xx. **404 means "no lease with this id,
ever"**; a closed lease stays readable, served from the hub or the local file (D1)."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.transcripts.repository import Turn
from blizzard.runner.transcripts.service import ResolvedTranscript
from blizzard.wire.transcript import TranscriptResponse, TurnView

router = APIRouter(prefix="/api", tags=["runner"])


def _turn_view(turn: Turn) -> TurnView:
    return TurnView(
        index=turn.index,
        kind=turn.kind,
        timestamp=iso_utc(turn.timestamp) if turn.timestamp is not None else None,
        text=turn.text,
        tool_name=turn.tool_name,
        tool_input=turn.tool_input,
        tool_output=turn.tool_output,
        truncated=turn.truncated,
    )


def _view(lease_id: str, resolved: ResolvedTranscript) -> TranscriptResponse:
    transcript = resolved.transcript
    return TranscriptResponse(
        lease_id=lease_id,
        session_id=transcript.session_id,
        available=transcript.available,
        reason=transcript.reason,
        turns=[_turn_view(turn) for turn in transcript.turns],
        truncated=transcript.truncated,
        provenance=resolved.provenance,
        hub_unreachable=resolved.hub_unreachable,
        dropped_turns=resolved.dropped,
    )


@router.get("/leases/{lease_id}/transcript", response_model=TranscriptResponse)
def get_transcript(lease_id: str, request: Request) -> TranscriptResponse:
    """The lease's resolved transcript — 404 iff no lease with this id ever existed."""
    service = RunnerWiring.of(request).transcripts()
    resolved = service.for_lease(lease_id)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no lease {lease_id}")
    return _view(lease_id, resolved)
