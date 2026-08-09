"""httpx adapter for the archived-transcript seam (blizzard#249, D4). See also
``runner/loop/internal/http_hub.py``, the runner's other outbound hub adapter.

All httpx and pydantic-wire usage is confined here: the hub's transcript-segments read is
fetched, validated, and translated into an :class:`ArchivedTranscript` — every outcome
reaches the caller as a value (``status="unreachable"`` on a transport failure); nothing
in this module ever raises past this boundary."""

from __future__ import annotations

import httpx

from blizzard.foundation.logging import get_logger
from blizzard.runner.transcripts.archived_repository import ArchivedTranscript, IReadArchivedTranscriptRepository
from blizzard.runner.transcripts.internal.segment_projection import select_turns, to_turn
from blizzard.runner.transcripts.repository import MAX_TURNS
from blizzard.wire.transcript_segment import LeaseTranscriptView

_log = get_logger("blizzard.runner.transcripts.archived")

#: The prefix every runner->hub call in this adapter goes under, matching the fleet
#: client's own (``runner/loop/internal/http_hub.py``).
_FLEET_API = "/api/fleet"

_EMPTY = ArchivedTranscript(status="empty", turns=[], truncated=False, dropped=0)
_REFUSED = ArchivedTranscript(status="refused", turns=[], truncated=False, dropped=0)
_UNREACHABLE = ArchivedTranscript(status="unreachable", turns=[], truncated=False, dropped=0)


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
            return _UNREACHABLE
        if resp.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            # A refusal is a definite answer, not a transport failure (D1) — the caller
            # falls back to local exactly like "holds nothing" would. Logged like every
            # other non-success branch here, so an unexpected refusal (a stale token, a
            # lease shipped under a prior runner_id) is not entirely silent.
            _log.error(
                "hub refused archived transcript read", chunk_id=chunk_id, node_id=node_id, status=resp.status_code
            )
            return _REFUSED
        if not resp.is_success:
            _log.error(
                "hub error for archived transcript",
                chunk_id=chunk_id,
                node_id=node_id,
                status=resp.status_code,
                body=resp.text[:500],
            )
            return _UNREACHABLE
        try:
            view = LeaseTranscriptView.model_validate(resp.json())
        except ValueError as exc:
            _log.error("malformed archived transcript body", chunk_id=chunk_id, node_id=node_id, error=str(exc))
            return _UNREACHABLE
        if not view.turns:
            # All-cap-rejected still carries no turns, but `view.truncated` says so.
            if not view.truncated:
                return _EMPTY
            return ArchivedTranscript(status="found", turns=[], truncated=True, dropped=0)
        kept, trailing = select_turns(view.turns)
        turns_truncated = len(kept) > MAX_TURNS
        capped = kept[-MAX_TURNS:] if turns_truncated else kept
        # Every drop attached to a kept survivor of the cap, plus the trailing drops after
        # the last kept turn overall — always in the rendered window, since nothing comes
        # after them for the cap to discard (fixes under-counting when nothing survives:
        # `capped` is then `[]` and `trailing` alone carries the whole count).
        dropped = sum(count for _, count in capped) + trailing
        return ArchivedTranscript(
            status="found",
            turns=[to_turn(t, i) for i, (t, _) in enumerate(capped)],
            truncated=turns_truncated or view.truncated,
            dropped=dropped,
        )


def _conforms_read_archived_transcript_repository(
    x: HttpArchivedTranscriptRepository,
) -> IReadArchivedTranscriptRepository:
    return x
