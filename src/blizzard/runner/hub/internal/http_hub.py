"""httpx adapter for the hub-client seam (package-private).

The reference :class:`~blizzard.runner.hub.client.IHubClient` and
:class:`~blizzard.runner.hub.client.ITokenIdentityReader` binding, the
:class:`~blizzard.runner.hub.client.IHubRunnerAdmin` one, the
:class:`~blizzard.runner.hub.client.IHostHubClient` one, and the auth presenting the runner's held
token. All httpx usage is confined here; a
transport failure or unexpected status is wrapped once into
:class:`~blizzard.runner.hub.client.HubClientError` (``bzh:structlog-logging``).
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Sequence

import httpx
from pydantic import BaseModel, TypeAdapter

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.hub.client import (
    BindingDeclaration,
    ChunkEndedError,
    ChunkNotFoundError,
    ClaimConflict,
    ClaimedRoute,
    ClaimRequest,
    DependencyDenial,
    FactPushAck,
    HostAddRefusalReason,
    HostAddRefused,
    HostFactPushAck,
    HostHeldRunner,
    HostRecord,
    HostRefusalReason,
    HostRefused,
    HostReport,
    HostRunnerRetireRefused,
    HubClientError,
    HubQuestion,
    IdentityRouteMissing,
    IHostHubClient,
    IHubClient,
    IHubRunnerAdmin,
    IncompatibleDenial,
    IssuedHost,
    IssuedIdentity,
    ITokenIdentityReader,
    OperatorTenant,
    PausedDenial,
    PushedFact,
    QueueBlock,
    QueueEntry,
    QueueWorkRef,
    RegistrationReply,
    RetiredHostRunner,
    RouteClaimOutcome,
    RunnerAddRefusalReason,
    RunnerAddRefused,
    SubscriptionDeclaration,
    TerminalDenial,
    TokenIdentity,
    TokenRefusal,
    TranscriptPush,
    TranscriptPushAck,
)
from blizzard.runner.hub.held_token import ICurrentHubToken
from blizzard.runner.hub.node_steps import (
    apply_reply_of,
    completion_submission,
    decision_submission,
    envelope_of,
)
from blizzard.runner.hub.served_projects import ServedProject
from blizzard.runner.node_steps.chunk_state import ChunkGate, ChunkPause, ChunkSpend, ChunkState
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.node_steps.submissions import ApplyReply, Completion, GateSubmission
from blizzard.wire.chunk import ChunkStatusView, HubAdvanceResponse
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import HostFactAck, HostFactBatch, RunnerFact, RunnerFactAck, RunnerFactBatch
from blizzard.wire.host import (
    HostAddRequest,
    HostAddResponse,
    HostRecordResponse,
    HostReportRequest,
    HostRunnerAddRequest,
    HostWorkspace,
)
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
    RunnerAddRequest,
    RunnerAddResponse,
    RunnerBinding,
    RunnerCapability,
    RunnerIdentityRefusal,
    RunnerIdentityView,
    RunnerRegistrationRequest,
    RunnerRegistrationResponse,
    RunnerRetireRequest,
    RunnerRetireResponse,
    RunnerSubscriptionDeclaration,
    RunnerView,
    ServedProjectView,
)
from blizzard.wire.transcript_segment import TranscriptSegmentAck, TranscriptSegmentBatch, TranscriptSegmentRecord
from blizzard.wire.user import CallerTenantsView

_log = get_logger("blizzard.runner.hub")

#: The prefix every runner->hub call in this client goes under.
_FLEET_API = "/api/fleet"

#: Overrides the shared client's own default timeout for this one call.
_TRANSCRIPT_PUSH_TIMEOUT_SECONDS = 5.0

#: Caps a single ``chunk-statuses`` GET's ``chunk_id`` query-param count, bounding its URL length.
_CHUNK_STATUSES_BATCH_LIMIT = 500

#: The operator route adding a runner — the one call this module makes outside the fleet API.
_RUNNERS_API = "/api/runners"

#: The hub-level route naming the tenants the operator may enter.
_ME_API = "/api/me"

#: The operator route adding a host.
_HOSTS_API = "/api/hosts"

#: The prefix every call under a host's own token goes under.
_HOST_API = f"{_FLEET_API}/host"

#: The header an operator call names its tenant in, by id.
TENANT_HEADER = "X-Blizzard-Tenant"

#: The statuses an add is refused with, each a reason the caller acts on rather than retries.
_ADD_REFUSALS: dict[int, RunnerAddRefusalReason] = {
    httpx.codes.UNAUTHORIZED: RunnerAddRefusalReason.SIGN_IN_REQUIRED,
    httpx.codes.FORBIDDEN: RunnerAddRefusalReason.FORBIDDEN,
    httpx.codes.NOT_FOUND: RunnerAddRefusalReason.UNSUPPORTED,
    httpx.codes.METHOD_NOT_ALLOWED: RunnerAddRefusalReason.UNSUPPORTED,
}

#: The statuses a host add is refused with, each a reason the caller acts on rather than retries.
_HOST_ADD_REFUSALS: dict[int, HostAddRefusalReason] = {
    httpx.codes.UNAUTHORIZED: HostAddRefusalReason.SIGN_IN_REQUIRED,
    httpx.codes.FORBIDDEN: HostAddRefusalReason.FORBIDDEN,
    httpx.codes.NOT_FOUND: HostAddRefusalReason.UNSUPPORTED,
    httpx.codes.METHOD_NOT_ALLOWED: HostAddRefusalReason.UNSUPPORTED,
    httpx.codes.CONFLICT: HostAddRefusalReason.NAME_TAKEN,
}

type _Refusal = Callable[[str, httpx.Response], HubClientError | None]


def _retire_held(operation: str, resp: httpx.Response) -> HubClientError | None:
    """A retire under the host refused while the runner still holds chunks (409)."""
    if resp.status_code != httpx.codes.CONFLICT:
        return None
    return HostRunnerRetireRefused(f"{operation} -> {resp.status_code}", detail=_detail(resp))


def _no_such_project(operation: str, resp: httpx.Response) -> HubClientError | None:
    """An add under the host refused for a named project the tenant does not hold (422 with the hub's text)."""
    if resp.status_code != httpx.codes.UNPROCESSABLE_ENTITY or not _text_detail(resp):
        return None
    return RunnerAddRefused(
        f"{operation} -> {resp.status_code}", reason=RunnerAddRefusalReason.NO_SUCH_PROJECT, detail=_detail(resp)
    )


#: The statuses a call under the host's own token is refused with, typed for the caller to branch on.
_HOST_REFUSALS: dict[int, HostRefusalReason] = {
    httpx.codes.UNAUTHORIZED: HostRefusalReason.CREDENTIAL_REFUSED,
    httpx.codes.FORBIDDEN: HostRefusalReason.CREDENTIAL_REFUSED,
    httpx.codes.NOT_FOUND: HostRefusalReason.NOT_FOUND,
}

#: The hub's refusal text for a named tenant the caller cannot enter — one text for an unknown, a
#: closed, and an unenterable tenant.
_NO_SUCH_TENANT = "no such tenant"

#: The ``detail.code`` of the hub's ``409`` when the caller may enter several tenants and named none.
_TENANT_REQUIRED = "tenant_required"


class HeldTokenAuth(httpx.Auth):
    """Authorizes each request with the token ``token`` holds when it is sent, so a client built once
    presents a token the runner takes up later."""

    def __init__(self, token: ICurrentHubToken) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers.update(self._token.auth_headers())
        yield request


class HttpHubClient:
    """The runner's hub API client over an injected ``httpx.Client``."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def peek_queue(
        self, capabilities: Sequence[HarnessCapability], *, policy: str, project: str | None = None
    ) -> list[QueueEntry]:
        request = QueuePeekRequest(capabilities=[_capability(c) for c in capabilities], policy=policy, project=project)
        resp = self._post(f"{_FLEET_API}/queue/peek", request.model_dump(mode="json"))
        return _queue(QueuePeekResponse.model_validate(resp.json()))

    def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
        body = RouteClaim(
            chunk_id=claim.chunk_id,
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

    def push_facts(self, facts: Sequence[PushedFact]) -> FactPushAck:
        batch = RunnerFactBatch(facts=[RunnerFact(seq=f.seq, kind=f.kind, payload=f.payload) for f in facts])
        resp = self._post(f"{_FLEET_API}/events", batch.model_dump(mode="json"))
        ack = RunnerFactAck.model_validate(resp.json())
        return FactPushAck(
            high_water=ack.high_water,
            applied=ack.applied,
            already_applied=ack.already_applied,
            rejected=ack.rejected,
            route_ended=ack.route_ended,
        )

    def push_transcripts(self, records: Sequence[TranscriptPush]) -> TranscriptPushAck:
        batch = TranscriptSegmentBatch(
            records=[TranscriptSegmentRecord.model_validate({"seq": r.seq, **r.body}) for r in records],
        )
        resp = self._post(
            f"{_FLEET_API}/transcripts", batch.model_dump(mode="json"), timeout=_TRANSCRIPT_PUSH_TIMEOUT_SECONDS
        )
        ack = TranscriptSegmentAck.model_validate(resp.json())
        return TranscriptPushAck(
            high_water=ack.high_water,
            applied=ack.applied,
            already_applied=ack.already_applied,
            capped=ack.capped,
            refused=ack.refused,
        )

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
        name: str,
        workspace_id: str,
        *,
        env_capacity: int | None = None,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        capabilities: tuple[HarnessCapability, ...] = (),
        subscriptions: tuple[SubscriptionDeclaration, ...] | None = (),
        gates: tuple[str, ...] = (),
        projects: tuple[str, ...] | None = None,
        bindings: tuple[BindingDeclaration, ...] | None = None,
        subscription: str | None = None,
    ) -> RegistrationReply:
        resp = self._post(
            f"{_FLEET_API}/runners",
            RunnerRegistrationRequest(
                name=name,
                workspace_id=workspace_id,
                env_capacity=env_capacity,
                url=url,
                redirect_uris=list(redirect_uris),
                capabilities=[_capability(c) for c in capabilities],
                subscriptions=(
                    [
                        RunnerSubscriptionDeclaration(slug=d.slug, name=d.name, provider=d.provider)
                        for d in subscriptions
                    ]
                    if subscriptions is not None
                    else None
                ),
                gates=list(gates),
                projects=list(projects) if projects is not None else None,
                bindings=(
                    [RunnerBinding(project=b.project, workspace_id=b.workspace_id, agents=b.agents) for b in bindings]
                    if bindings is not None
                    else None
                ),
                subscription=subscription,
            ).model_dump(mode="json"),
        )
        reply = RunnerRegistrationResponse.model_validate(resp.json())
        return RegistrationReply(
            runner_id=reply.runner_id,
            # A hub reply carrying no name leaves the one this registration declared.
            runner_name=reply.runner_name if reply.runner_name is not None else name,
            first_registration=reply.first_registration,
            tenant_id=reply.tenant_id,
            tenant_name=reply.tenant_name,
            projects=tuple(_served(p) for p in reply.projects) if reply.projects is not None else None,
        )

    def fetch_runner_paused(self, runner_id: str) -> bool:
        resp = self._get(f"{_FLEET_API}/runners/{runner_id}")
        return bool(RunnerView.model_validate(resp.json()).hub_paused)

    def identity(self) -> TokenIdentity | TokenRefusal:
        operation = f"GET {_FLEET_API}/identity"
        try:
            resp = self._client.get(f"{_FLEET_API}/identity")
        except httpx.HTTPError as exc:
            raise self._wrap(exc, operation) from exc
        if resp.status_code == httpx.codes.UNAUTHORIZED:
            refusal = _identity_refusal(resp)
            if refusal is not None:
                _log.info("hub call refused", operation=operation, status=resp.status_code, reason=refusal.reason)
                return refusal
        self._raise_for_status(resp, operation, not_found_as=IdentityRouteMissing)
        try:
            view = RunnerIdentityView.model_validate(resp.json())
        except ValueError as exc:
            raise _malformed(operation, exc) from exc
        return TokenIdentity(
            runner_id=view.runner_id,
            runner_name=view.runner_name,
            tenant_id=view.tenant_id,
            tenant_name=view.tenant_name,
        )

    def rekey_route_token(self, chunk_id: str) -> str:
        resp = self._post(
            f"{_FLEET_API}/chunks/{chunk_id}/route-token", None, ended_on_conflict=True, ended_on_not_found=True
        )
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
        self,
        path: str,
        body: object,
        *,
        timeout: float | None = None,
        ended_on_conflict: bool = False,
        ended_on_not_found: bool = False,
    ) -> httpx.Response:
        try:
            if timeout is not None:
                resp = self._client.post(path, json=body, timeout=timeout)
            else:
                resp = self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise self._wrap(exc, f"POST {path}") from exc
        self._raise_for_status(
            resp, f"POST {path}", ended_on_conflict=ended_on_conflict, ended_on_not_found=ended_on_not_found
        )
        return resp

    def _raise_for_status(
        self,
        resp: httpx.Response,
        operation: str,
        *,
        not_found_as: type[HubClientError] | None = None,
        ended_on_conflict: bool = False,
        ended_on_not_found: bool = False,
    ) -> None:
        if resp.is_success:
            return
        message = f"{operation} -> {resp.status_code}: {resp.text[:200]}"
        # A status decoded to a typed terminal outcome is expected: the caller owns its handling and
        # its log level. Only a status left unclassified is a hub-call failure worth an error.
        if not_found_as is not None and resp.status_code == httpx.codes.NOT_FOUND:
            _log.info("hub call refused", operation=operation, status=resp.status_code)
            raise not_found_as(message)
        if ended_on_not_found and resp.status_code == httpx.codes.NOT_FOUND:
            _log.info("hub call refused", operation=operation, status=resp.status_code)
            raise ChunkEndedError(message, detail=_detail(resp))
        if ended_on_conflict and resp.status_code == httpx.codes.CONFLICT:
            _log.info("hub call refused", operation=operation, status=resp.status_code)
            raise ChunkEndedError(message, detail=_detail(resp))
        _log.error("hub call failed", operation=operation, status=resp.status_code, body=resp.text[:500])
        raise HubClientError(message)

    @staticmethod
    def _wrap(exc: httpx.HTTPError, operation: str) -> HubClientError:
        _log.error("hub unreachable", operation=operation, detail=str(exc))
        return HubClientError(f"{operation} failed: {exc}")


class HttpHubRunnerAdmin:
    """Runner add and the operator's tenants over an injected ``httpx.Client``, sending
    ``operator_token`` as a Bearer credential when set and no credential otherwise."""

    def __init__(self, client: httpx.Client, *, operator_token: str | None) -> None:
        self._client = client
        self._operator_token = operator_token

    def add_runner(
        self, name: str, *, tenant_id: str | None = None, projects: tuple[str, ...] | None = None
    ) -> IssuedIdentity:
        operation = f"POST {_RUNNERS_API}"
        headers = self._headers()
        if tenant_id is not None:
            headers[TENANT_HEADER] = tenant_id
        request = RunnerAddRequest(name=name, projects=list(projects) if projects is not None else None)
        try:
            resp = self._client.post(
                _RUNNERS_API, json=request.model_dump(mode="json", exclude={"host_id"}), headers=headers
            )
        except httpx.HTTPError as exc:
            _log.error("hub unreachable", operation=operation, detail=str(exc))
            raise HubClientError(f"{operation} failed: {exc}") from exc
        reason = _add_refusal(resp)
        if reason is not None:
            _log.info("hub call refused", operation=operation, status=resp.status_code, reason=reason)
            raise RunnerAddRefused(f"{operation} -> {resp.status_code}", reason=reason, detail=_detail(resp))
        if not resp.is_success:
            _log.error("hub call failed", operation=operation, status=resp.status_code, body=resp.text[:500])
            raise HubClientError(f"{operation} -> {resp.status_code}: {resp.text[:200]}")
        try:
            added = RunnerAddResponse.model_validate(resp.json())
        except ValueError as exc:
            raise _malformed(operation, exc) from exc
        return IssuedIdentity(runner_id=added.runner_id, runner_name=added.runner_name, token=added.token)

    def tenants(self) -> list[OperatorTenant]:
        operation = f"GET {_ME_API}"
        try:
            resp = self._client.get(_ME_API, headers=self._headers())
        except httpx.HTTPError as exc:
            _log.error("hub unreachable", operation=operation, detail=str(exc))
            raise HubClientError(f"{operation} failed: {exc}") from exc
        if not resp.is_success:
            _log.error("hub call failed", operation=operation, status=resp.status_code, body=resp.text[:500])
            raise HubClientError(f"{operation} -> {resp.status_code}: {resp.text[:200]}")
        try:
            me = CallerTenantsView.model_validate(resp.json())
        except ValueError as exc:
            raise _malformed(operation, exc) from exc
        return [OperatorTenant(tenant_id=t.tenant_id, name=t.name) for t in me.tenants]

    def add_host(self, name: str, *, tenant_id: str | None = None, adopt: tuple[str, ...] = ()) -> IssuedHost:
        operation = f"POST {_HOSTS_API}"
        headers = self._headers()
        if tenant_id is not None:
            headers[TENANT_HEADER] = tenant_id
        try:
            resp = self._client.post(
                _HOSTS_API,
                json=HostAddRequest(name=name, adopt=list(adopt)).model_dump(mode="json", exclude_defaults=True),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            _log.error("hub unreachable", operation=operation, detail=str(exc))
            raise HubClientError(f"{operation} failed: {exc}") from exc
        reason = _host_add_refusal(resp)
        if reason is not None:
            _log.info("hub call refused", operation=operation, status=resp.status_code, reason=reason)
            raise HostAddRefused(f"{operation} -> {resp.status_code}", reason=reason, detail=_detail(resp))
        if not resp.is_success:
            _log.error("hub call failed", operation=operation, status=resp.status_code, body=resp.text[:500])
            raise HubClientError(f"{operation} -> {resp.status_code}: {resp.text[:200]}")
        try:
            added = HostAddResponse.model_validate(resp.json())
        except ValueError as exc:
            raise _malformed(operation, exc) from exc
        return IssuedHost(host_id=added.host_id, host_name=added.host_name, token=added.token)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._operator_token}"} if self._operator_token else {}


def _host_add_refusal(resp: httpx.Response) -> HostAddRefusalReason | None:
    """The reason a host add's answer refuses it, or ``None`` when it is no refusal the caller acts on.
    A ``404`` carrying the hub's tenant text is the tenant refusal, and a ``409`` carrying the
    tenant-required code is that one; any other ``404`` is a hub that predates hosts, and any other
    ``409`` a name a live host holds."""
    if resp.status_code == httpx.codes.NOT_FOUND and _detail(resp) == _NO_SUCH_TENANT:
        return HostAddRefusalReason.NO_SUCH_TENANT
    if resp.status_code == httpx.codes.CONFLICT and _refusal_code(resp) == _TENANT_REQUIRED:
        return HostAddRefusalReason.TENANT_REQUIRED
    return _HOST_ADD_REFUSALS.get(resp.status_code)


class HttpHostHubClient:
    """The host's own hub calls over an injected ``httpx.Client``, presenting ``token`` — the host's
    bearer token — on each, whatever credential the client itself carries."""

    def __init__(self, client: httpx.Client, *, token: str) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}

    def report(self, report: HostReport) -> HostRecord | HostRefused:
        body = HostReportRequest(
            url=report.url,
            redirect_uris=list(report.redirect_uris),
            workspaces=[
                HostWorkspace(workspace_id=w.workspace_id, env_capacity=w.env_capacity) for w in report.workspaces
            ],
            subscriptions=[
                RunnerSubscriptionDeclaration(slug=d.slug, name=d.name, provider=d.provider)
                for d in report.subscriptions
            ],
        )
        resp = self._send("POST", _HOST_API, body.model_dump(mode="json", exclude={"name"}))
        return resp if isinstance(resp, HostRefused) else _host_record(_validated(HostRecordResponse, resp, _HOST_API))

    def record(self) -> HostRecord | HostRefused:
        resp = self._send("GET", _HOST_API)
        return resp if isinstance(resp, HostRefused) else _host_record(_validated(HostRecordResponse, resp, _HOST_API))

    def push_facts(self, facts: Sequence[PushedFact]) -> HostFactPushAck | HostRefused:
        path = f"{_HOST_API}/events"
        batch = HostFactBatch(facts=[RunnerFact(seq=f.seq, kind=f.kind, payload=f.payload) for f in facts])
        resp = self._send("POST", path, batch.model_dump(mode="json"))
        if isinstance(resp, HostRefused):
            return resp
        ack = _validated(HostFactAck, resp, path)
        return HostFactPushAck(
            high_water=ack.high_water, applied=ack.applied, already_applied=ack.already_applied, rejected=ack.rejected
        )

    def add_runner(self, name: str, *, projects: tuple[str, ...] | None = None) -> IssuedIdentity | HostRefused:
        path = f"{_HOST_API}/runners"
        request = HostRunnerAddRequest(name=name, projects=list(projects) if projects is not None else None)
        resp = self._send("POST", path, request.model_dump(mode="json"), refused=_no_such_project)
        if isinstance(resp, HostRefused):
            return resp
        added = _validated(RunnerAddResponse, resp, path)
        return IssuedIdentity(runner_id=added.runner_id, runner_name=added.runner_name, token=added.token)

    def retire_runner(self, runner_id: str, *, force: bool = False) -> RetiredHostRunner | HostRefused:
        path = f"{_HOST_API}/runners/{runner_id}/retire"
        resp = self._send("POST", path, RunnerRetireRequest(force=force).model_dump(mode="json"), refused=_retire_held)
        if isinstance(resp, HostRefused):
            return resp
        retired = _validated(RunnerRetireResponse, resp, path)
        return RetiredHostRunner(
            runner_id=retired.runner.runner_id, released_chunk_ids=tuple(retired.released_chunk_ids)
        )

    def _send(
        self, method: str, path: str, body: object = None, *, refused: _Refusal | None = None
    ) -> httpx.Response | HostRefused:
        """The hub's answer, or its typed refusal; an answer ``refused`` types as a call's own refusal raises it."""
        operation = f"{method} {path}"
        try:
            resp = self._client.request(method, path, json=body, headers=self._headers)
        except httpx.HTTPError as exc:
            _log.error("hub unreachable", operation=operation, detail=str(exc))
            raise HubClientError(f"{operation} failed: {exc}") from exc
        if resp.is_success:
            return resp
        reason = _HOST_REFUSALS.get(resp.status_code)
        if reason is not None:
            _log.info("hub call refused", operation=operation, status=resp.status_code, reason=reason)
            return HostRefused(reason=reason, status=resp.status_code, detail=_detail(resp))
        if refused is not None and (error := refused(operation, resp)) is not None:
            _log.info("hub call refused", operation=operation, status=resp.status_code)
            raise error
        _log.error("hub call failed", operation=operation, status=resp.status_code, body=resp.text[:500])
        raise HubClientError(f"{operation} -> {resp.status_code}: {resp.text[:200]}")


def _validated[T: BaseModel](model: type[T], resp: httpx.Response, path: str) -> T:
    try:
        return model.model_validate(resp.json())
    except ValueError as exc:
        raise _malformed(f"{resp.request.method} {path}", exc) from exc


def _host_record(wire: HostRecordResponse) -> HostRecord:
    return HostRecord(
        host_id=wire.host_id,
        tenant_id=wire.tenant_id,
        host_name=wire.host_name,
        runners=tuple(HostHeldRunner(runner_id=r.runner_id, runner_name=r.runner_name) for r in wire.runners),
    )


def _add_refusal(resp: httpx.Response) -> RunnerAddRefusalReason | None:
    """The reason an add's answer refuses it, or ``None`` when it is no refusal the caller acts on.
    A ``404`` is the tenant refusal only when it carries the hub's tenant text — a hub that predates
    adding runners answers its own; a ``409`` is a refusal only when it is the tenant one; a ``422``
    is the project refusal only when its detail is the hub's text — a malformed request's carries a
    list of field errors instead."""
    if resp.status_code == httpx.codes.NOT_FOUND and _detail(resp) == _NO_SUCH_TENANT:
        return RunnerAddRefusalReason.NO_SUCH_TENANT
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        return RunnerAddRefusalReason.NO_SUCH_PROJECT if _text_detail(resp) else None
    if resp.status_code == httpx.codes.CONFLICT:
        return RunnerAddRefusalReason.TENANT_REQUIRED if _refusal_code(resp) == _TENANT_REQUIRED else None
    return _ADD_REFUSALS.get(resp.status_code)


def _refusal_code(resp: httpx.Response) -> str | None:
    """The ``detail.code`` of a structured FastAPI refusal, or ``None`` when it carries none."""
    try:
        body = resp.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    return code if isinstance(code, str) else None


def _conforms_hub_client(x: HttpHubClient) -> IHubClient:
    return x


def _conforms_token_identity_reader(x: HttpHubClient) -> ITokenIdentityReader:
    return x


def _conforms_hub_runner_admin(x: HttpHubRunnerAdmin) -> IHubRunnerAdmin:
    return x


def _conforms_host_hub_client(x: HttpHostHubClient) -> IHostHubClient:
    return x


def _identity_refusal(resp: httpx.Response) -> TokenRefusal | None:
    """The identity route's typed ``401``, or ``None`` for a ``401`` that is not one — a proxy's,
    say — which the caller treats as any other failed call."""
    try:
        refusal = RunnerIdentityRefusal.model_validate(resp.json())
    except ValueError:
        return None
    return TokenRefusal(reason=refusal.reason, runner_id=refusal.runner_id)


def _malformed(operation: str, exc: ValueError) -> HubClientError:
    _log.error("hub call failed", operation=operation, detail=f"malformed reply: {exc}")
    return HubClientError(f"{operation}: malformed reply: {exc}")


def _detail(resp: httpx.Response) -> str:
    """A FastAPI refusal's ``detail`` text, or the raw body when it carries none."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail is not None else resp.text[:200]


def _text_detail(resp: httpx.Response) -> bool:
    """Whether a refusal's ``detail`` is the hub's own text, as opposed to a list of field errors."""
    try:
        body = resp.json()
    except ValueError:
        return False
    return isinstance(body, dict) and isinstance(body.get("detail"), str)


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
        chunk_id=wire.chunk_id,
        incompatible_runner_id=wire.incompatible_runner_id,
        detail=wire.detail,
        reason=wire.reason,
    )


def _served(wire: ServedProjectView) -> ServedProject:
    return ServedProject(entry=wire.entry, project_id=wire.project_id, slug=wire.slug, name=wire.name)


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
            project_id=e.project_id,
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
        project_id=wire.project_id,
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
