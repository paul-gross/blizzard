"""The runner-local transcript read — ``GET /api/leases/{lease_id}/transcript`` (issue #29).

Path matches ``POST /api/leases/{lease_id}/asks``'s nesting: a transcript is a
sub-resource of a lease. **The URL stays lease-keyed**, not session-keyed, even
though the read path is session-keyed underneath —
``session_id`` is nullable (a ``spawning`` lease has none yet), and a
``/api/sessions/{sid}/…`` route could only 404 for that state, collapsing "agent is
starting up" into "transcript not found". Lease-keyed models ``spawning`` natively.

**200-always with an in-band ``reason``**: a missing or unreadable transcript
is a normal state of a healthy agent (still spawning, session not yet flushed,
retained history whose file rotated away), never a 5xx. **404 means "no lease with
this id, ever"** — copy is ``no lease {id}``, deliberately **not** ``api/asks.py``'s
``no active lease {id}``: that route's ``active_lease()`` precedent filters to
active *by design* (asks target live workers) and would wrongly 404 a closed
lease's transcript here (closed leases stay readable). **503** when the
service is unwired, matching ``api/leases.py``'s copy/pattern.

Read-only over its wiring (``bzh:controller-read-only``): the edge holds only the
composition-root-wired :class:`LocalTranscriptService`, no repository at all.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.transcripts.repository import Transcript, Turn
from blizzard.runner.transcripts.service import LocalTranscriptService
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


def _view(lease_id: str, transcript: Transcript) -> TranscriptResponse:
    return TranscriptResponse(
        lease_id=lease_id,
        session_id=transcript.session_id,
        available=transcript.available,
        reason=transcript.reason,
        turns=[_turn_view(turn) for turn in transcript.turns],
        truncated=transcript.truncated,
    )


@router.get("/leases/{lease_id}/transcript", response_model=TranscriptResponse)
def get_transcript(lease_id: str, request: Request) -> TranscriptResponse:
    """The lease's parsed transcript — 404 iff no lease with this id ever existed."""
    service: LocalTranscriptService | None = getattr(request.app.state, "transcripts", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="transcript service not wired — start via `blizzard runner host`",
        )
    transcript = service.for_lease(lease_id)
    if transcript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no lease {lease_id}")
    return _view(lease_id, transcript)
