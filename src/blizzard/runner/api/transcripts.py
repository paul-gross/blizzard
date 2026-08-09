"""The runner-local transcript read — ``GET /api/leases/{lease_id}/transcript`` (issue #29).

**The URL stays lease-keyed**, not session-keyed: ``session_id`` is nullable, so a session-keyed route
could only 404 a ``spawning`` lease, collapsing "agent is starting up" into "transcript not found".
**200-always with an in-band ``reason``** — a missing or unreadable transcript is a normal state of a
healthy agent, never a 5xx. **404 means "no lease with this id, ever"**; a closed lease stays readable."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.transcripts.repository import Sidechain, ToolCall, Transcript, Turn
from blizzard.wire.transcript import TranscriptResponse
from blizzard.wire.transcript_segment import SidechainSegmentView, ToolCallSegmentView, TurnSegmentView

router = APIRouter(prefix="/api", tags=["runner"])


def _tool_view(tool: ToolCall) -> ToolCallSegmentView:
    return ToolCallSegmentView(
        name=tool.name,
        input=dict(tool.input),
        input_unparsed=tool.input_unparsed,
        input_shape=tool.input_shape,
        tool_use_id=tool.tool_use_id,
        output=tool.output,
        output_truncated=tool.output_truncated,
    )


def _sidechain_view(sidechain: Sidechain) -> SidechainSegmentView:
    return SidechainSegmentView(
        agent_id=sidechain.agent_id,
        agent_type=sidechain.agent_type,
        link=sidechain.link,
        turns=[_turn_view(turn) for turn in sidechain.turns],
    )


def _turn_view(turn: Turn) -> TurnSegmentView:
    return TurnSegmentView(
        index=turn.index,
        kind=turn.kind,
        timestamp=iso_utc(turn.timestamp) if turn.timestamp is not None else None,
        text=turn.text,
        tool=_tool_view(turn.tool) if turn.tool is not None else None,
        thinking_redacted=turn.thinking_redacted,
        sidechain=_sidechain_view(turn.sidechain) if turn.sidechain is not None else None,
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
    service = RunnerWiring.of(request).transcripts()
    transcript = service.for_lease(lease_id)
    if transcript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no lease {lease_id}")
    return _view(lease_id, transcript)
