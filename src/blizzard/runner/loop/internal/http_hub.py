"""httpx adapter for the hub-client seam (package-private).

The reference :class:`~blizzard.runner.loop.hub.IHubClient` binding. All httpx usage is
confined here; a transport failure or unexpected status is wrapped once into
:class:`~blizzard.runner.loop.hub.HubClientError` (``bzh:structlog-logging``).
"""

from __future__ import annotations

from collections.abc import Iterable

import httpx
from pydantic import TypeAdapter

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError, IHubClient, RouteClaimOutcome
from blizzard.wire.chunk import ChunkStatusView, HubAdvanceResponse
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import RunnerFactAck, RunnerFactBatch
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekRequest, QueuePeekResponse
from blizzard.wire.route import (
    RouteClaim,
    RouteClaimConflict,
    RouteClaimDependencyDenial,
    RouteClaimIncompatibleDenial,
    RouteClaimPausedDenial,
    RouteClaimResponse,
    RouteClaimTerminalDenial,
    RouteTokenRekeyResponse,
)
from blizzard.wire.runner import RunnerCapability, RunnerRegistrationRequest, RunnerView
from blizzard.wire.transcript_segment import TranscriptSegmentAck, TranscriptSegmentBatch

_log = get_logger("blizzard.runner.hub")

#: The prefix every runner->hub call in this client goes under (issue #87).
_FLEET_API = "/api/fleet"

#: Overrides the shared client's own default timeout for this one call (issue #246).
_TRANSCRIPT_PUSH_TIMEOUT_SECONDS = 5.0

#: Caps a single ``chunk-statuses`` GET's ``chunk_id`` query-param count — a tick's primed id
#: set (``tick.py``'s ``_primed_chunk_ids``) carries no fleet-wide bound, so one oversized
#: batch must not become one oversized URL (`drain.py`'s ``_DRAIN_LIMIT`` precedent).
_CHUNK_STATUSES_BATCH_LIMIT = 500


class HttpHubClient:
    """The runner's hub API client over an injected ``httpx.Client``."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def peek_queue(self, request: QueuePeekRequest) -> QueuePeekResponse:
        path = f"{_FLEET_API}/queue/peek"
        try:
            resp = self._client.post(path, json=request.model_dump(mode="json"))
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"POST {path}") from exc
        if resp.status_code == httpx.codes.UNAUTHORIZED:
            # No token, or the matched verb's own always-raising demand for a principal
            # (D7) — the legacy verb serves this caller in every auth mode instead.
            return QueuePeekResponse.model_validate(self._get(path).json())
        self._raise_for_status(resp, f"POST {path}")
        return QueuePeekResponse.model_validate(resp.json())

    def claim_route(self, claim: RouteClaim) -> RouteClaimOutcome:
        try:
            resp = self._client.post(f"{_FLEET_API}/routes", json=claim.model_dump(mode="json"))
        except httpx.HTTPError as exc:
            raise self._wrap(exc, "POST /fleet/routes") from exc
        if resp.status_code == httpx.codes.CONFLICT:
            body = resp.json()
            # Four distinct 409 shapes share the status code: a race loss
            # (`held_by_runner_id`), a terminal denial (`status`, issue #118), a
            # dependency denial (`prerequisite_chunk_id`, blizzard#458), and an
            # incompatibility denial (`incompatible_runner_id`, blizzard#433 D9) —
            # told apart by body.
            if "status" in body:
                return RouteClaimOutcome(denied_terminal=RouteClaimTerminalDenial.model_validate(body))
            if "prerequisite_chunk_id" in body:
                return RouteClaimOutcome(denied_dependency=RouteClaimDependencyDenial.model_validate(body))
            if "incompatible_runner_id" in body:
                return RouteClaimOutcome(denied_incompatible=RouteClaimIncompatibleDenial.model_validate(body))
            return RouteClaimOutcome(conflict=RouteClaimConflict.model_validate(body))
        if resp.status_code == httpx.codes.FORBIDDEN:
            return RouteClaimOutcome(denied_paused=RouteClaimPausedDenial.model_validate(resp.json()))
        self._raise_for_status(resp, "POST /fleet/routes")
        return RouteClaimOutcome(claimed=RouteClaimResponse.model_validate(resp.json()))

    def submit_completion(self, chunk_id: str, submission: CompletionSubmission) -> ApplyResponse:
        resp = self._post(f"{_FLEET_API}/chunks/{chunk_id}/completions", submission.model_dump(mode="json"))
        return ApplyResponse.model_validate(resp.json())

    def submit_decision(self, chunk_id: str, submission: DecisionSubmission) -> ApplyResponse:
        resp = self._post(f"{_FLEET_API}/chunks/{chunk_id}/decisions", submission.model_dump(mode="json"))
        return ApplyResponse.model_validate(resp.json())

    def push_facts(self, batch: RunnerFactBatch) -> RunnerFactAck:
        resp = self._post(f"{_FLEET_API}/events", batch.model_dump(mode="json"))
        return RunnerFactAck.model_validate(resp.json())

    def push_transcripts(self, batch: TranscriptSegmentBatch) -> TranscriptSegmentAck:
        resp = self._post(
            f"{_FLEET_API}/transcripts", batch.model_dump(mode="json"), timeout=_TRANSCRIPT_PUSH_TIMEOUT_SECONDS
        )
        return TranscriptSegmentAck.model_validate(resp.json())

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        resp = self._get(f"{_FLEET_API}/chunks/{chunk_id}/envelope", not_found_as=ChunkNotFoundError)
        return NodeEnvelope.model_validate(resp.json())

    def chunk_statuses(self, chunk_ids: Iterable[str]) -> dict[str, ChunkStatusView]:
        ids = list(dict.fromkeys(chunk_ids))
        result: dict[str, ChunkStatusView] = {}
        for start in range(0, len(ids), _CHUNK_STATUSES_BATCH_LIMIT):
            batch = ids[start : start + _CHUNK_STATUSES_BATCH_LIMIT]
            resp = self._get(f"{_FLEET_API}/chunk-statuses", params={"chunk_id": batch})
            for view in TypeAdapter(list[ChunkStatusView]).validate_python(resp.json()):
                result[view.chunk_id] = view
        return result

    def hub_advance(self, chunk_id: str) -> HubAdvanceResponse:
        path = f"{_FLEET_API}/chunks/{chunk_id}/hub-advance"
        try:
            resp = self._client.post(path)
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"POST {path}") from exc
        self._raise_for_status(resp, f"POST {path}")
        return HubAdvanceResponse.model_validate(resp.json())

    def get_question(self, question_id: str) -> QuestionView:
        resp = self._get(f"{_FLEET_API}/questions/{question_id}")
        return QuestionView.model_validate(resp.json())

    def register_runner(
        self,
        runner_id: str,
        workspace_id: str,
        *,
        env_capacity: int | None = None,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        capabilities: tuple[RunnerCapability, ...] = (),
    ) -> None:
        self._post(
            f"{_FLEET_API}/runners",
            RunnerRegistrationRequest(
                runner_id=runner_id,
                workspace_id=workspace_id,
                env_capacity=env_capacity,
                url=url,
                redirect_uris=list(redirect_uris),
                capabilities=list(capabilities),
            ).model_dump(mode="json"),
        )

    def fetch_runner_paused(self, runner_id: str) -> bool:
        resp = self._get(f"{_FLEET_API}/runners/{runner_id}")
        return bool(RunnerView.model_validate(resp.json()).hub_paused)

    def rekey_route_token(self, chunk_id: str) -> RouteTokenRekeyResponse:
        resp = self._post(f"{_FLEET_API}/chunks/{chunk_id}/route-token", None)
        return RouteTokenRekeyResponse.model_validate(resp.json())

    # --- plumbing -----------------------------------------------------------

    def _get(
        self,
        path: str,
        *,
        params: dict[str, list[str]] | None = None,
        not_found_as: type[HubClientError] | None = None,
    ) -> httpx.Response:
        try:
            resp = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"GET {path}") from exc
        self._raise_for_status(resp, f"GET {path}", not_found_as=not_found_as)
        return resp

    def _post(self, path: str, body: object, *, timeout: float | None = None) -> httpx.Response:
        try:
            if timeout is not None:
                resp = self._client.post(path, json=body, timeout=timeout)
            else:
                resp = self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"POST {path}") from exc
        self._raise_for_status(resp, f"POST {path}")
        return resp

    def _raise_for_status(
        self, resp: httpx.Response, operation: str, *, not_found_as: type[HubClientError] | None = None
    ) -> None:
        if resp.is_success:
            return
        _log.error("hub call failed", operation=operation, status=resp.status_code, body=resp.text[:500])
        if not_found_as is not None and resp.status_code == httpx.codes.NOT_FOUND:
            raise not_found_as(f"{operation} -> {resp.status_code}: {resp.text[:200]}")
        raise HubClientError(f"{operation} -> {resp.status_code}: {resp.text[:200]}")

    @staticmethod
    def _wrap(exc: httpx.HTTPError, operation: str) -> HubClientError:
        _log.error("hub unreachable", operation=operation, detail=str(exc))
        return HubClientError(f"{operation} failed: {exc}")


def _conforms_hub_client(x: HttpHubClient) -> IHubClient:
    return x
