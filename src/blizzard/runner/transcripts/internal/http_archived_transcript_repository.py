"""httpx adapter for the archived-transcript seam (blizzard#249, D4) — a sibling of
``runner/loop/internal/http_hub.py``, the runner's other outbound hub adapter.

All httpx and pydantic-wire usage is confined here: the hub's transcript-segments read is
fetched, validated, and translated into an :class:`ArchivedTranscript`. Every outcome
reaches the caller as a value; nothing here ever raises past this boundary."""

from __future__ import annotations

import httpx

from blizzard.foundation.logging import get_logger
from blizzard.runner.transcripts.archived_repository import (
    ArchivedTranscript,
    ArchivedTranscriptStatus,
    IReadArchivedTranscriptRepository,
)
from blizzard.runner.transcripts.internal.projected_transcript_repository import MAX_TURNS
from blizzard.runner.transcripts.internal.segment_projection import to_turn
from blizzard.wire.transcript_segment import LeaseTranscriptView

_log = get_logger("blizzard.runner.transcripts.archived")

#: The prefix every runner->hub call in this adapter goes under, matching the fleet
#: client's own (``runner/loop/internal/http_hub.py``).
_FLEET_API = "/api/fleet"


def _answer(status: ArchivedTranscriptStatus) -> ArchivedTranscript:
    """A turn-less outcome. Built per call rather than shared as a module singleton: the
    dataclass is frozen but its ``turns`` list is not, and one aliased list is a hazard."""
    return ArchivedTranscript(status=status, turns=[], truncated=False)


class HttpArchivedTranscriptRepository:
    """Implements :class:`IReadArchivedTranscriptRepository` over an injected
    ``httpx.Client`` already carrying the runner's own auth headers (``config.auth_headers()``,
    ``bzh:dependency-inversion``)."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def read_turns(self, *, chunk_id: str, node_id: str, epoch: int) -> ArchivedTranscript:
        path = f"{_FLEET_API}/chunks/{chunk_id}/transcript-segments"
        try:
            resp = self._client.get(path, params={"node_id": node_id, "epoch": epoch})
        except httpx.HTTPError as exc:
            _log.error("hub unreachable for archived transcript", chunk_id=chunk_id, node_id=node_id, error=str(exc))
            return _answer("unreachable")
        if resp.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            # A refusal is a definite answer, not a transport failure (D1) — the caller
            # falls back to local exactly like "holds nothing" would. Logged, never silent.
            _log.error(
                "hub refused archived transcript read", chunk_id=chunk_id, node_id=node_id, status=resp.status_code
            )
            return _answer("refused")
        if not resp.is_success:
            _log.error(
                "hub error for archived transcript",
                chunk_id=chunk_id,
                node_id=node_id,
                status=resp.status_code,
                body=resp.text[:500],
            )
            return _answer("unreachable")
        try:
            view = LeaseTranscriptView.model_validate(resp.json())
        except ValueError as exc:
            _log.error("malformed archived transcript body", chunk_id=chunk_id, node_id=node_id, error=str(exc))
            return _answer("unreachable")
        if not view.turns:
            # Nothing renderable — including the all-cap-rejected case, which the caller
            # resolves to local exactly like "holds nothing", so the two need not differ.
            return _answer("empty")
        # The same recency cap the local read applies, deliberately shared rather than
        # re-chosen: one panel renders both homes, so both must bound the payload alike.
        capped = len(view.turns) > MAX_TURNS
        window = view.turns[-MAX_TURNS:] if capped else view.turns
        return ArchivedTranscript(
            status="found",
            turns=[to_turn(turn, i) for i, turn in enumerate(window)],
            truncated=capped or view.truncated,
        )


def _conforms_read_archived_transcript_repository(
    x: HttpArchivedTranscriptRepository,
) -> IReadArchivedTranscriptRepository:
    return x
