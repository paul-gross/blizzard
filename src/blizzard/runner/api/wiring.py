"""Resolving what the composition root wired onto ``app.state`` and each runner's seams (``bzh:dependency-injection``).

Every seam is optional, so a route asks for what it needs and is refused with a ``503`` naming it, never served on
nothing. No accessor here resolves a write-capable store or bundle (``bzh:controller-read-only``):
:meth:`RunnerWiring.read_stores` is the one many-concept read, and every mutation resolves its own single-concept
service instead."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import httpx
from fastapi import Path, Request, status
from fastapi.exceptions import HTTPException
from starlette.datastructures import State

from blizzard.foundation.clock import IClock, IMonotonicClock
from blizzard.foundation.events.broker import EventBroker
from blizzard.foundation.flags import Flags
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.roles import collaborator, dto
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.auth.jwks_cache import IJwksCache
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.environments.workspaces import IWorkspaces
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.health_cache import IReadHarnessHealth
from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.runner.harness.workspace_prompts import WorkspacePromptService
from blizzard.runner.host_config import HaltedRunner
from blizzard.runner.hub.client import ITokenIdentityReader
from blizzard.runner.hub.held_token import ICurrentHubToken
from blizzard.runner.hub.host_identity import HostIdentityHolder, ICurrentHostIdentity
from blizzard.runner.hub.host_registration import HostRegistrationHolder, ICurrentHostRegistration
from blizzard.runner.hub.identity import ICurrentIdentityVerdict, IIdentityOnContact
from blizzard.runner.leases.activity import LocalLeaseService
from blizzard.runner.leases.asks import AskService
from blizzard.runner.leases.liveness import LeaseLivenessService
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.session import LeaseSessionService
from blizzard.runner.leases.worker_lease import WorkerLease
from blizzard.runner.lifecycle.judgement.git_commit_declaration import GitCommitDeclarationService
from blizzard.runner.lifecycle.takeover import TakeoverService
from blizzard.runner.operator.attachments import AttachmentService
from blizzard.runner.operator.requeue import RequeueService
from blizzard.runner.selftest.service import SelfTestService
from blizzard.runner.status.view import RunnerStatusService
from blizzard.runner.stores import HostReadStores, IRunnerBinding, RunnerReadStores
from blizzard.runner.throttle.pause import PauseService
from blizzard.runner.tracing.receiving import TelemetryReceiver
from blizzard.runner.tracing.replay import LeaseTraceReplay
from blizzard.runner.tracing.status import LeaseTraceStatusReader
from blizzard.runner.transcripts.service import TranscriptService

if TYPE_CHECKING:
    # `src/blizzard/runner/api/federation.py` resolves its own collaborators through this module.
    from blizzard.runner.api.federation import FederationSettings, HubAuthModeCache

_STORE = "runner store"


@dataclass(frozen=True)
class ServedRunner:
    """One runner the host serves: its hub-minted id, its name, and the seams its routes resolve."""

    runner_id: str
    name: str
    seams: State


@collaborator
@dataclass(frozen=True)
class RunnerRegistry:
    """The host's runners, keyed by hub-minted id: those it serves, and those it holds halted."""

    served: tuple[ServedRunner, ...] = ()
    halted: tuple[HaltedRunner, ...] = ()

    def resolve(self, runner_id: str) -> ServedRunner:
        """The served runner ``runner_id`` names; ``404`` for an id the host does not hold, ``503`` naming the
        reasons for one it holds halted."""
        for runner in self.served:
            if runner.runner_id == runner_id:
                return runner
        for halted in self.halted:
            if halted.id == runner_id:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=halted.describe())
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no runner {runner_id} on this host")


@dto
@dataclass(frozen=True)
class ServedWorkspace:
    """One workspace the host serves: its environment pool, and its static prompt — what resolves while no runtime
    override stands."""

    workspace_id: str
    env_pool: tuple[str, ...]
    max_environments: int
    configured_prompt: str = ""


def bind_runner(request: Request, runner_id: Annotated[str, Path(description="The runner's hub-minted id.")]) -> None:
    """The dependency every runner-owned router mounts behind: binds the runner ``runner_id`` names, so
    :meth:`RunnerWiring.of` resolves that runner's seams."""
    registry: RunnerRegistry = getattr(request.app.state, "runners", None) or RunnerRegistry()
    request.state.runner = registry.resolve(runner_id).seams


@dataclass(frozen=True)
class RunnerWiring:
    """One route's view of the wired runner — each accessor resolves its seam or refuses,
    bar the ``maybe_`` pair, for the two reads that degrade instead."""

    state: State
    #: The seams :func:`bind_runner` bound for the request's runner; ``None`` on a host route, which reads the host's.
    runner: State | None = None

    @classmethod
    def of(cls, request: Request) -> RunnerWiring:
        return cls(request.app.state, getattr(request.state, "runner", None))

    def _seam(self, name: str) -> Any:
        """``name`` from the bound runner's seams when it carries one, else from the host's."""
        if self.runner is not None and hasattr(self.runner, name):
            return getattr(self.runner, name)
        return getattr(self.state, name, None)

    def runners(self) -> RunnerRegistry:
        """Every runner the host holds, served or halted."""
        registry: RunnerRegistry | None = self._seam("runners")
        return registry if registry is not None else RunnerRegistry()

    def for_runner(self, runner: ServedRunner) -> RunnerWiring:
        """This wiring, bound to ``runner`` — how a host read composes each runner's own seams."""
        return RunnerWiring(self.state, runner.seams)

    def flags(self) -> Flags:
        """The host's resolved flags."""
        flags: Flags | None = self._seam("flags")
        return flags if flags is not None else self._refuse("flags")

    def workspaces(self) -> tuple[ServedWorkspace, ...]:
        workspaces: tuple[ServedWorkspace, ...] | None = self._seam("workspaces")
        return workspaces or ()

    def workspace(self, workspace_id: str) -> ServedWorkspace:
        """The served workspace ``workspace_id`` names; ``404`` for one the host does not declare."""
        for workspace in self.workspaces():
            if workspace.workspace_id == workspace_id:
                return workspace
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no workspace {workspace_id} on this host")

    def subscriptions(self) -> tuple[SubscriptionDeclaration, ...]:
        """The subscriptions the host declares."""
        declared: tuple[SubscriptionDeclaration, ...] | None = self._seam("subscriptions")
        return declared or ()

    def config(self) -> RunnerConfig:
        config = self.maybe_config()
        return config if config is not None else self._refuse(_STORE)

    def clock(self) -> IClock:
        clock: IClock | None = self._seam("clock")
        return clock if clock is not None else self._refuse(_STORE)

    def federation(self) -> FederationSettings:
        settings: FederationSettings | None = self._seam("federation")
        return settings if settings is not None else self._refuse("federation settings")

    def hub_auth_mode(self) -> HubAuthModeCache:
        cache: HubAuthModeCache | None = self._seam("hub_auth_mode")
        return cache if cache is not None else self._refuse("hub auth-mode cache")

    def jwks_cache(self) -> IJwksCache:
        cache: IJwksCache | None = self._seam("jwks_cache")
        return cache if cache is not None else self._refuse("JWKS cache")

    def jti_cache(self) -> IJtiCache:
        cache: IJtiCache | None = self._seam("jti_cache")
        return cache if cache is not None else self._refuse("JTI cache")

    def session_secret(self) -> bytes:
        secret: bytes | None = self._seam("session_secret")
        return secret if secret is not None else self._refuse("session secret")

    def trusted_proxies(self) -> TrustedProxies:
        proxies: TrustedProxies | None = self._seam("trusted_proxies")
        return proxies if proxies is not None else self._refuse("trusted proxies")

    def hub_proxy_client(self) -> httpx.Client:
        client: httpx.Client | None = self._seam("hub_proxy_client")
        return client if client is not None else self._refuse("hub proxy client")

    def hub_retry_clock(self) -> IMonotonicClock:
        clock: IMonotonicClock | None = self._seam("hub_retry_clock")
        return clock if clock is not None else self._refuse("hub proxy retry clock")

    def read_stores(self) -> RunnerReadStores:
        stores = self.maybe_read_stores()
        return stores if stores is not None else self._refuse(_STORE)

    def host_read_stores(self) -> HostReadStores:
        stores = self.maybe_host_read_stores()
        return stores if stores is not None else self._refuse(_STORE)

    def worker_lease(self, lease_id: str) -> Lease:
        """The lease a worker verb may act against: the active lease, or — when the ordinary
        active lease is gone — the one an open takeover names. An open takeover
        is a second, independent source of worker-verb authorization, not a re-mint: the
        resolved record's id, node and epoch are unchanged from whatever they already were."""
        return self.worker_lease_standing(lease_id).lease

    def worker_lease_standing(self, lease_id: str) -> WorkerLease:
        """:meth:`worker_lease`, with whether it resolved to the active lease or to the
        closed reference lease an open takeover names."""
        stores = self.read_stores()
        active = stores.lease_record.active_lease(lease_id)
        if active is not None:
            return WorkerLease(lease=active, active=True)
        reference = stores.takeover.lease_for_open_takeover(lease_id)
        if reference is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no active lease or open takeover for lease {lease_id}",
            )
        return WorkerLease(lease=reference, active=False)

    def status(self) -> RunnerStatusService:
        service: RunnerStatusService | None = self._seam("runner_status")
        return service if service is not None else self._refuse("runner status service")

    def maybe_status(self) -> RunnerStatusService | None:
        """The runner status service; ``None`` on a store-free runner — never refused, so a host read lists it still."""
        return self._seam("runner_status")

    def trace_status(self) -> LeaseTraceStatusReader:
        reader: LeaseTraceStatusReader | None = self._seam("trace_status")
        return reader if reader is not None else self._refuse("trace status reader")

    def trace_replay(self) -> LeaseTraceReplay:
        replay: LeaseTraceReplay | None = self._seam("trace_replay")
        return replay if replay is not None else self._refuse("trace replay")

    def telemetry_receiver(self) -> TelemetryReceiver:
        receiver: TelemetryReceiver | None = self._seam("telemetry_receiver")
        return receiver if receiver is not None else self._refuse("telemetry receiver")

    def leases(self) -> LocalLeaseService:
        service: LocalLeaseService | None = self._seam("leases")
        return service if service is not None else self._refuse("lease service")

    def transcripts(self) -> TranscriptService:
        service: TranscriptService | None = self._seam("transcripts")
        return service if service is not None else self._refuse("transcript service")

    def takeover(self) -> TakeoverService:
        service: TakeoverService | None = self._seam("takeover")
        return service if service is not None else self._refuse("takeover service")

    def requeue(self) -> RequeueService:
        service: RequeueService | None = self._seam("requeue")
        return service if service is not None else self._refuse("requeue service")

    def attachments(self) -> AttachmentService:
        service: AttachmentService | None = self._seam("attachments")
        return service if service is not None else self._refuse("attachment service")

    def git_commits(self) -> GitCommitDeclarationService:
        service: GitCommitDeclarationService | None = self._seam("git_commit_declarations")
        return service if service is not None else self._refuse("git-commit declaration service")

    def selftests(self) -> SelfTestService:
        service: SelfTestService | None = self._seam("selftests")
        return service if service is not None else self._refuse("selftest service")

    def harnesses(self) -> IHarnessRegistry:
        registry: IHarnessRegistry | None = self._seam("harnesses")
        return registry if registry is not None else self._refuse("harness registry")

    def harness_health(self) -> IReadHarnessHealth:
        """The runner's own health cache, read-only; refuses like every other
        unwired collaborator when the caller wired none."""
        cache: IReadHarnessHealth | None = self._seam("harness_health")
        return cache if cache is not None else self._refuse("harness health cache")

    def asks(self) -> AskService:
        service: AskService | None = self._seam("asks")
        return service if service is not None else self._refuse("ask service")

    def pause(self) -> PauseService:
        service: PauseService | None = self._seam("pause")
        return service if service is not None else self._refuse("pause service")

    def lease_liveness(self) -> LeaseLivenessService:
        service: LeaseLivenessService | None = self._seam("lease_liveness")
        return service if service is not None else self._refuse("lease liveness service")

    def lease_sessions(self) -> LeaseSessionService:
        service: LeaseSessionService | None = self._seam("lease_sessions")
        return service if service is not None else self._refuse("lease session service")

    def workspace_registry(self) -> IWorkspaces:
        registry = self.maybe_workspace_registry()
        return registry if registry is not None else self._refuse("workspace registry")

    def maybe_workspace_registry(self) -> IWorkspaces | None:
        """The host's workspace registry; ``None`` on an app built without one."""
        return getattr(self.state, "workspace_registry", None)

    def workspace_prompts(self) -> WorkspacePromptService:
        service: WorkspacePromptService | None = self._seam("workspace_prompts")
        return service if service is not None else self._refuse("workspace prompt service")

    def events(self) -> IRunnerEventPublisher | None:
        """The publish seam, typed against :mod:`~blizzard.runner.events.publisher`'s Protocol.
        ``None`` when none is wired — never refused."""
        return self._seam("events")

    def maybe_event_broker(self) -> EventBroker | None:
        """The SSE broker the stream route reads; ``None`` when no stream is wired — never refused."""
        return self._seam("event_broker")

    def maybe_shutdown(self) -> asyncio.Event | None:
        """The event ``_lifespan`` sets on shutdown; ``None`` where the app was built without one."""
        return self._seam("shutdown")

    def maybe_readiness(self) -> ReadinessService | None:
        """The readiness service; ``None`` on the store-free app, which reports not ready — never refused."""
        return self._seam("readiness")

    def maybe_identity_verdict(self) -> ICurrentIdentityVerdict | None:
        """The runner's identity verdict; ``None`` on a runner built without one."""
        return self._seam("identity_verdict")

    def host_registration(self) -> ICurrentHostRegistration:
        """Where the host stands with its hub."""
        registration: ICurrentHostRegistration | None = self._seam("host_registration")
        return registration if registration is not None else HostRegistrationHolder.for_host(registered=False)

    def host_identity(self) -> ICurrentHostIdentity:
        """The identity the host's first answered report recorded."""
        identity: ICurrentHostIdentity | None = self._seam("host_identity")
        return identity if identity is not None else HostIdentityHolder()

    def maybe_identity_check(self) -> IIdentityOnContact | None:
        return self._seam("identity_check")

    def maybe_token_identity(self) -> ITokenIdentityReader | None:
        return self._seam("token_identity")

    def maybe_hub_token(self) -> ICurrentHubToken | None:
        return self._seam("hub_token")

    def maybe_config(self) -> RunnerConfig | None:
        return self._seam("config")

    def maybe_read_stores(self) -> RunnerReadStores | None:
        return self._seam("runner_read_stores")

    def maybe_host_read_stores(self) -> HostReadStores | None:
        return self._seam("host_read_stores")

    def require_bound(self) -> None:
        """Refuse with a ``503`` when the store is bound to no runner. The host binds each store to its configured
        id before it serves, so this only guards an app wired with a binding that was never bound; an app wired
        without a binding checks nothing."""
        binding: IRunnerBinding | None = self._seam("binding")
        if binding is not None and binding.runner_id is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="runner store is bound to no runner id — the host binds it from its configured id at startup",
            )

    @staticmethod
    def _refuse(what: str) -> NoReturn:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{what} not wired — start via `blizzard runner host`",
        )
