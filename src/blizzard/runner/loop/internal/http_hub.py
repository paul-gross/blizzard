"""httpx adapter for the hub-client seam (package-private).

The reference :class:`~blizzard.runner.loop.hub.IHubClient` binding. All httpx
usage is confined here; a transport failure or unexpected status is wrapped once
into :class:`~blizzard.runner.loop.hub.HubClientError` (``bzh:structlog-logging``).
The injected ``httpx.Client`` is the seam tests substitute with an
``httpx.MockTransport``-backed client, so the loop is exercised against a fake hub
with no live daemon.
"""

from __future__ import annotations

import httpx

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError, IHubClient, RouteClaimOutcome
from blizzard.wire.chunk import ChunkDetail
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import EscalationReport, LeaseMintReport, RunnerFactAck, RunnerFactBatch
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekResponse
from blizzard.wire.route import RouteClaim, RouteClaimConflict, RouteClaimPausedDenial, RouteClaimResponse
from blizzard.wire.runner import RunnerRegistrationRequest, RunnerView

_log = get_logger("blizzard.runner.hub")

_API = "/api"


class HttpHubClient:
    """The runner's hub API client over an injected ``httpx.Client``."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def peek_queue(self) -> QueuePeekResponse:
        resp = self._get(f"{_API}/queue/peek")
        return QueuePeekResponse.model_validate(resp.json())

    def claim_route(self, claim: RouteClaim) -> RouteClaimOutcome:
        try:
            resp = self._client.post(f"{_API}/routes", json=claim.model_dump(mode="json"))
        except httpx.HTTPError as exc:
            raise self._wrap(exc, "POST /routes") from exc
        if resp.status_code == httpx.codes.CONFLICT:
            return RouteClaimOutcome(conflict=RouteClaimConflict.model_validate(resp.json()))
        if resp.status_code == httpx.codes.FORBIDDEN:
            return RouteClaimOutcome(denied_paused=RouteClaimPausedDenial.model_validate(resp.json()))
        self._raise_for_status(resp, "POST /routes")
        return RouteClaimOutcome(claimed=RouteClaimResponse.model_validate(resp.json()))

    def submit_completion(self, chunk_id: str, submission: CompletionSubmission) -> ApplyResponse:
        resp = self._post(f"{_API}/chunks/{chunk_id}/completions", submission.model_dump(mode="json"))
        return ApplyResponse.model_validate(resp.json())

    def submit_decision(self, chunk_id: str, submission: DecisionSubmission) -> ApplyResponse:
        resp = self._post(f"{_API}/chunks/{chunk_id}/decisions", submission.model_dump(mode="json"))
        return ApplyResponse.model_validate(resp.json())

    def push_facts(self, batch: RunnerFactBatch) -> RunnerFactAck:
        resp = self._post(f"{_API}/events", batch.model_dump(mode="json"))
        return RunnerFactAck.model_validate(resp.json())

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        resp = self._get(f"{_API}/chunks/{chunk_id}/envelope", not_found_as=ChunkNotFoundError)
        return NodeEnvelope.model_validate(resp.json())

    def get_chunk(self, chunk_id: str) -> ChunkDetail:
        resp = self._get(f"{_API}/chunks/{chunk_id}", not_found_as=ChunkNotFoundError)
        return ChunkDetail.model_validate(resp.json())

    def get_question(self, question_id: str) -> QuestionView:
        resp = self._get(f"{_API}/questions/{question_id}")
        return QuestionView.model_validate(resp.json())

    def register_runner(self, runner_id: str, workspace_id: str) -> None:
        self._post(
            f"{_API}/runners",
            RunnerRegistrationRequest(runner_id=runner_id, workspace_id=workspace_id).model_dump(mode="json"),
        )

    def fetch_runner_paused(self, runner_id: str) -> bool:
        resp = self._get(f"{_API}/runners/{runner_id}")
        return bool(RunnerView.model_validate(resp.json()).hub_paused)

    def report_lease(self, chunk_id: str, *, epoch: int, runner_id: str) -> None:
        self._post(
            f"{_API}/chunks/{chunk_id}/leases",
            LeaseMintReport(epoch=epoch, runner_id=runner_id).model_dump(mode="json"),
        )

    def report_escalation(self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str) -> None:
        self._post(
            f"{_API}/chunks/{chunk_id}/escalations",
            EscalationReport(epoch=epoch, runner_id=runner_id, takeover_command=takeover_command).model_dump(
                mode="json"
            ),
        )

    # --- plumbing -----------------------------------------------------------

    def _get(self, path: str, *, not_found_as: type[HubClientError] | None = None) -> httpx.Response:
        try:
            resp = self._client.get(path)
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"GET {path}") from exc
        self._raise_for_status(resp, f"GET {path}", not_found_as=not_found_as)
        return resp

    def _post(self, path: str, body: object) -> httpx.Response:
        try:
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
