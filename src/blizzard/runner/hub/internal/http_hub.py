"""httpx adapter for the hub-client seam (package-private).

The reference :class:`~blizzard.runner.hub.client.IHubClient` binding. All httpx usage is
confined here; a transport failure or unexpected status is wrapped once into
:class:`~blizzard.runner.hub.client.HubClientError` (``bzh:structlog-logging``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import httpx
from pydantic import TypeAdapter

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.hub.client import (
    ChunkEndedError,
    ChunkNotFoundError,
    ClaimConflict,
    ClaimedRoute,
    ClaimRequest,
    DependencyDenial,
    FactPushAck,
    HubClientError,
    HubQuestion,
    IHubClient,
    IncompatibleDenial,
    PausedDenial,
    PushedFact,
    QueueBlock,
    QueueEntry,
    QueueWorkRef,
    RouteClaimOutcome,
    SubscriptionDeclaration,
    TerminalDenial,
)
from blizzard.runner.hub.node_steps import (
    apply_reply_of,
    completion_submission,
    decision_submission,
    envelope_of,
)
from blizzard.runner.node_steps.chunk_state import ChunkGate, ChunkPause, ChunkSpend, ChunkState
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.node_steps.submissions import ApplyReply, Completion, GateSubmission
from blizzard.wire.chunk import ChunkStatusView, HubAdvanceResponse
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import RunnerFact, RunnerFactAck, RunnerFactBatch
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
from blizzard.wire.runner import (
    RunnerCapability,
    RunnerRegistrationRequest,
    RunnerSubscriptionDeclaration,
    RunnerView,
)
from blizzard.wire.transcript_segment import TranscriptSegmentAck, TranscriptSegmentBatch

_log = get_logger("blizzard.runner.hub")

#: The prefix every runner->hub call in this client goes under.
_FLEET_API = "/api/fleet"

#: Overrides the shared client's own default timeout for this one call.
_TRANSCRIPT_PUSH_TIMEOUT_SECONDS = 5.0

#: Caps a single ``chunk-statuses`` GET's ``chunk_id`` query-param count — a tick's primed id
#: set (``tick.py``'s ``_primed_chunk_ids``) carries no fleet-wide bound, so one oversized
#: batch must not become one oversized URL (`drain.py`'s ``_DRAIN_LIMIT`` precedent).
_CHUNK_STATUSES_BATCH_LIMIT = 500


class HttpHubClient:
    """The runner's hub API client over an injected ``httpx.Client``."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def peek_queue(self, capabilities: Sequence[HarnessCapability], *, policy: str) -> list[QueueEntry]:
        path = f"{_FLEET_API}/queue/peek"
        request = QueuePeekRequest(capabilities=[_capability(c) for c in capabilities], policy=policy)
        try:
            resp = self._client.post(path, json=request.model_dump(mode="json"))
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"POST {path}") from exc
        if resp.status_code == httpx.codes.UNAUTHORIZED:
            # No token, or the matched verb's own always-raising demand for a principal
            # — the legacy verb serves this caller in every auth mode instead.
            return _queue(QueuePeekResponse.model_validate(self._get(path).json()))
        self._raise_for_status(resp, f"POST {path}")
        return _queue(QueuePeekResponse.model_validate(resp.json()))

    def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
        body = RouteClaim(
            chunk_id=claim.chunk_id,
            runner_id=claim.runner_id,
            workspace_id=claim.workspace_id,
            environment_ids=claim.environment_ids,
        ).model_dump(mode="json")
        try:
            resp = self._client.post(f"{_FLEET_API}/routes", json=body)
        except httpx.HTTPError as exc:
            raise self._wrap(exc, "POST /fleet/routes") from exc
        if resp.status_code == httpx.codes.CONFLICT:
            body = resp.json()
            # Four distinct 409 shapes share the status code — race loss, terminal,
            # dependency, and incompatibility denials — told apart by which field is in body.
            if "status" in body:
                return RouteClaimOutcome(denied_terminal=_terminal(RouteClaimTerminalDenial.model_validate(body)))
            if "prerequisite_chunk_id" in body:
                return RouteClaimOutcome(denied_dependency=_dependency(RouteClaimDependencyDenial.model_validate(body)))
            if "incompatible_runner_id" in body:
                return RouteClaimOutcome(
                    denied_incompatible=_incompatible(RouteClaimIncompatibleDenial.model_validate(body))
                )
            return RouteClaimOutcome(conflict=_conflict(RouteClaimConflict.model_validate(body)))
        if resp.status_code == httpx.codes.FORBIDDEN:
            return RouteClaimOutcome(denied_paused=_paused(RouteClaimPausedDenial.model_validate(resp.json())))
        self._raise_for_status(resp, "POST /fleet/routes")
        return RouteClaimOutcome(claimed=_claimed(RouteClaimResponse.model_validate(resp.json())))

    def submit_completion(self, chunk_id: str, completion: Completion) -> ApplyReply:
        body = completion_submission(completion).model_dump(mode="json")
        resp = self._post(f"{_FLEET_API}/chunks/{chunk_id}/completions", body)
        return apply_reply_of(ApplyResponse.model_validate(resp.json()))

    def submit_decision(self, chunk_id: str, gate: GateSubmission) -> ApplyReply:
        resp = self._post(
            f"{_FLEET_API}/chunks/{chunk_id}/decisions", decision_submission(gate).model_dump(mode="json")
        )
        return apply_reply_of(ApplyResponse.model_validate(resp.json()))

    def push_facts(self, runner_id: str, facts: Sequence[PushedFact]) -> FactPushAck:
        batch = RunnerFactBatch(
            runner_id=runner_id, facts=[RunnerFact(seq=f.seq, kind=f.kind, payload=f.payload) for f in facts]
        )
        resp = self._post(f"{_FLEET_API}/events", batch.model_dump(mode="json"))
        ack = RunnerFactAck.model_validate(resp.json())
        return FactPushAck(
            high_water=ack.high_water,
            applied=ack.applied,
            already_applied=ack.already_applied,
            rejected=ack.rejected,
        )

    def push_transcripts(self, batch: TranscriptSegmentBatch) -> TranscriptSegmentAck:
        resp = self._post(
            f"{_FLEET_API}/transcripts", batch.model_dump(mode="json"), timeout=_TRANSCRIPT_PUSH_TIMEOUT_SECONDS
        )
        return TranscriptSegmentAck.model_validate(resp.json())

    def get_envelope(self, chunk_id: str) -> Envelope:
        resp = self._get(
            f"{_FLEET_API}/chunks/{chunk_id}/envelope", not_found_as=ChunkNotFoundError, ended_on_conflict=True
        )
        return envelope_of(NodeEnvelope.model_validate(resp.json()))

    def chunk_statuses(self, chunk_ids: Iterable[str]) -> dict[str, ChunkState]:
        ids = list(dict.fromkeys(chunk_ids))
        result: dict[str, ChunkState] = {}
        for start in range(0, len(ids), _CHUNK_STATUSES_BATCH_LIMIT):
            batch = ids[start : start + _CHUNK_STATUSES_BATCH_LIMIT]
            resp = self._get(f"{_FLEET_API}/chunk-statuses", params={"chunk_id": batch})
            for view in TypeAdapter(list[ChunkStatusView]).validate_python(resp.json()):
                result[view.chunk_id] = _chunk_state(view)
        return result

    def hub_advance(self, chunk_id: str) -> HubAdvanceResponse:
        path = f"{_FLEET_API}/chunks/{chunk_id}/hub-advance"
        try:
            resp = self._client.post(path)
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"POST {path}") from exc
        self._raise_for_status(resp, f"POST {path}")
        return HubAdvanceResponse.model_validate(resp.json())

    def get_question(self, question_id: str) -> HubQuestion:
        resp = self._get(f"{_FLEET_API}/questions/{question_id}")
        return _question(QuestionView.model_validate(resp.json()))

    def register_runner(
        self,
        runner_id: str,
        workspace_id: str,
        *,
        env_capacity: int | None = None,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        capabilities: tuple[HarnessCapability, ...] = (),
        subscriptions: tuple[SubscriptionDeclaration, ...] = (),
        gates: tuple[str, ...] = (),
    ) -> None:
        self._post(
            f"{_FLEET_API}/runners",
            RunnerRegistrationRequest(
                runner_id=runner_id,
                workspace_id=workspace_id,
                env_capacity=env_capacity,
                url=url,
                redirect_uris=list(redirect_uris),
                capabilities=[_capability(c) for c in capabilities],
                subscriptions=[
                    RunnerSubscriptionDeclaration(slug=d.slug, name=d.name, provider=d.provider) for d in subscriptions
                ],
                gates=list(gates),
            ).model_dump(mode="json"),
        )

    def fetch_runner_paused(self, runner_id: str) -> bool:
        resp = self._get(f"{_FLEET_API}/runners/{runner_id}")
        return bool(RunnerView.model_validate(resp.json()).hub_paused)

    def rekey_route_token(self, chunk_id: str) -> str:
        resp = self._post(f"{_FLEET_API}/chunks/{chunk_id}/route-token", None, ended_on_conflict=True)
        return RouteTokenRekeyResponse.model_validate(resp.json()).route_token

    # --- plumbing -----------------------------------------------------------

    def _get(
        self,
        path: str,
        *,
        params: dict[str, list[str]] | None = None,
        not_found_as: type[HubClientError] | None = None,
        ended_on_conflict: bool = False,
    ) -> httpx.Response:
        try:
            resp = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"GET {path}") from exc
        self._raise_for_status(resp, f"GET {path}", not_found_as=not_found_as, ended_on_conflict=ended_on_conflict)
        return resp

    def _post(
        self, path: str, body: object, *, timeout: float | None = None, ended_on_conflict: bool = False
    ) -> httpx.Response:
        try:
            if timeout is not None:
                resp = self._client.post(path, json=body, timeout=timeout)
            else:
                resp = self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"POST {path}") from exc
        self._raise_for_status(resp, f"POST {path}", ended_on_conflict=ended_on_conflict)
        return resp

    def _raise_for_status(
        self,
        resp: httpx.Response,
        operation: str,
        *,
        not_found_as: type[HubClientError] | None = None,
        ended_on_conflict: bool = False,
    ) -> None:
        if resp.is_success:
            return
        _log.error("hub call failed", operation=operation, status=resp.status_code, body=resp.text[:500])
        if not_found_as is not None and resp.status_code == httpx.codes.NOT_FOUND:
            raise not_found_as(f"{operation} -> {resp.status_code}: {resp.text[:200]}")
        if ended_on_conflict and resp.status_code == httpx.codes.CONFLICT:
            raise ChunkEndedError(f"{operation} -> {resp.status_code}: {resp.text[:200]}", detail=_detail(resp))
        raise HubClientError(f"{operation} -> {resp.status_code}: {resp.text[:200]}")

    @staticmethod
    def _wrap(exc: httpx.HTTPError, operation: str) -> HubClientError:
        _log.error("hub unreachable", operation=operation, detail=str(exc))
        return HubClientError(f"{operation} failed: {exc}")


def _conforms_hub_client(x: HttpHubClient) -> IHubClient:
    return x


def _detail(resp: httpx.Response) -> str:
    """A FastAPI refusal's ``detail`` text, or the raw body when it carries none."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail is not None else resp.text[:200]


def _claimed(wire: RouteClaimResponse) -> ClaimedRoute:
    return ClaimedRoute(
        chunk_id=wire.chunk_id,
        runner_id=wire.runner_id,
        workspace_id=wire.workspace_id,
        environment_ids=list(wire.environment_ids),
        envelope=envelope_of(wire.envelope),
        route_token=wire.route_token,
    )


def _conflict(wire: RouteClaimConflict) -> ClaimConflict:
    return ClaimConflict(chunk_id=wire.chunk_id, held_by_runner_id=wire.held_by_runner_id, detail=wire.detail)


def _terminal(wire: RouteClaimTerminalDenial) -> TerminalDenial:
    return TerminalDenial(chunk_id=wire.chunk_id, status=wire.status, detail=wire.detail)


def _dependency(wire: RouteClaimDependencyDenial) -> DependencyDenial:
    return DependencyDenial(
        chunk_id=wire.chunk_id, prerequisite_chunk_id=wire.prerequisite_chunk_id, detail=wire.detail
    )


def _incompatible(wire: RouteClaimIncompatibleDenial) -> IncompatibleDenial:
    return IncompatibleDenial(
        chunk_id=wire.chunk_id, incompatible_runner_id=wire.incompatible_runner_id, detail=wire.detail
    )


def _paused(wire: RouteClaimPausedDenial) -> PausedDenial:
    return PausedDenial(chunk_id=wire.chunk_id, runner_id=wire.runner_id, detail=wire.detail)


def _capability(capability: HarnessCapability) -> RunnerCapability:
    return RunnerCapability(
        harness_id=capability.harness_id,
        version=capability.version,
        tiers=list(capability.tiers),
        default=capability.default,
        available=capability.available,
    )


def _queue(wire: QueuePeekResponse) -> list[QueueEntry]:
    return [
        QueueEntry(
            chunk_id=e.chunk_id,
            graph_id=e.graph_id,
            position=e.position,
            work_refs=[QueueWorkRef(source=r.source, ref=r.ref) for r in e.work_refs],
            blocked=QueueBlock(prerequisite_chunk_id=e.blocked.prerequisite_chunk_id, unmet_count=e.blocked.unmet_count)
            if e.blocked is not None
            else None,
        )
        for e in wire.entries
    ]


def _chunk_state(wire: ChunkStatusView) -> ChunkState:
    cost, decision, pause = wire.cost, wire.decision, wire.pause
    return ChunkState(
        chunk_id=wire.chunk_id,
        status=wire.status,
        route_runner_id=wire.route_runner_id,
        pause=ChunkPause(by=pause.by, set_at=pause.set_at) if pause is not None else None,
        latest_epoch=wire.latest_epoch,
        restart_epochs=list(wire.restart_epochs),
        cost=ChunkSpend(
            input_tokens=cost.input_tokens,
            output_tokens=cost.output_tokens,
            cache_read_tokens=cost.cache_read_tokens,
            cache_create_tokens=cost.cache_create_tokens,
            cost_usd=cost.cost_usd,
            cost_partial=cost.cost_partial,
            estimated_cost_usd=cost.estimated_cost_usd,
            billed_partial=cost.billed_partial,
        ),
        decision=ChunkGate(
            decision_id=decision.decision_id,
            node_id=decision.node_id,
            epoch=decision.epoch,
            resolved_choice=decision.resolved_choice,
            transitioned=decision.transitioned,
        )
        if decision is not None
        else None,
    )


def _question(wire: QuestionView) -> HubQuestion:
    return HubQuestion(
        question_id=wire.question_id,
        chunk_id=wire.chunk_id,
        runner_id=wire.runner_id,
        epoch=wire.epoch,
        question=wire.question,
        asked_at=wire.asked_at,
        node_id=wire.node_id,
        session_id=wire.session_id,
        harness_id=wire.harness_id,
        options=list(wire.options),
        answered=wire.answered,
        answer=wire.answer,
        answered_by=wire.answered_by,
        answered_at=wire.answered_at,
        delivered=wire.delivered,
        delivered_at=wire.delivered_at,
    )
