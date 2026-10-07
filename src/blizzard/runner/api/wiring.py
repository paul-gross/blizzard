"""Resolving what the composition root wired onto ``app.state`` (``bzh:dependency-injection``).

Every seam is optional, so a
route asks for what it needs and is refused with a ``503`` naming it, never served on nothing.
No accessor here resolves a write-capable store or bundle (``bzh:controller-read-only``):
:meth:`RunnerWiring.read_stores` is the one many-concept read, and every
mutation resolves its own single-concept service instead."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

import httpx
from fastapi import Request, status
from fastapi.exceptions import HTTPException
from starlette.datastructures import State

from blizzard.foundation.clock import IClock, IMonotonicClock
from blizzard.foundation.events.broker import EventBroker
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.auth.jwks_cache import IJwksCache
from blizzard.runner.config import RunnerConfig
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.health_cache import IReadHarnessHealth
from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.runner.harness.workspace_prompts import WorkspacePromptService
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
from blizzard.runner.stores import RunnerReadStores
from blizzard.runner.throttle.pause import PauseService
from blizzard.runner.tracing.receiving import TelemetryReceiver
from blizzard.runner.tracing.replay import LeaseTraceReplay
from blizzard.runner.tracing.status import LeaseTraceStatusReader
from blizzard.runner.transcripts.service import TranscriptService

if TYPE_CHECKING:
    # `api/federation.py` resolves its own collaborators through this module.
    from blizzard.runner.api.federation import FederationSettings, HubAuthModeCache

_STORE = "runner store"


@dataclass(frozen=True)
class RunnerWiring:
    """One route's view of the wired runner — each accessor resolves its seam or refuses,
    bar the ``maybe_`` pair, for the two reads that degrade instead."""

    state: State

    @classmethod
    def of(cls, request: Request) -> RunnerWiring:
        return cls(request.app.state)

    def config(self) -> RunnerConfig:
        config = self.maybe_config()
        return config if config is not None else self._refuse(_STORE)

    def clock(self) -> IClock:
        clock: IClock | None = getattr(self.state, "clock", None)
        return clock if clock is not None else self._refuse(_STORE)

    def federation(self) -> FederationSettings:
        settings: FederationSettings | None = getattr(self.state, "federation", None)
        return settings if settings is not None else self._refuse("federation settings")

    def hub_auth_mode(self) -> HubAuthModeCache:
        cache: HubAuthModeCache | None = getattr(self.state, "hub_auth_mode", None)
        return cache if cache is not None else self._refuse("hub auth-mode cache")

    def jwks_cache(self) -> IJwksCache:
        cache: IJwksCache | None = getattr(self.state, "jwks_cache", None)
        return cache if cache is not None else self._refuse("JWKS cache")

    def jti_cache(self) -> IJtiCache:
        cache: IJtiCache | None = getattr(self.state, "jti_cache", None)
        return cache if cache is not None else self._refuse("JTI cache")

    def session_secret(self) -> bytes:
        secret: bytes | None = getattr(self.state, "session_secret", None)
        return secret if secret is not None else self._refuse("session secret")

    def trusted_proxies(self) -> TrustedProxies:
        proxies: TrustedProxies | None = getattr(self.state, "trusted_proxies", None)
        return proxies if proxies is not None else self._refuse("trusted proxies")

    def hub_proxy_client(self) -> httpx.Client:
        client: httpx.Client | None = getattr(self.state, "hub_proxy_client", None)
        return client if client is not None else self._refuse("hub proxy client")

    def hub_retry_clock(self) -> IMonotonicClock:
        clock: IMonotonicClock | None = getattr(self.state, "hub_retry_clock", None)
        return clock if clock is not None else self._refuse("hub proxy retry clock")

    def read_stores(self) -> RunnerReadStores:
        stores = self.maybe_read_stores()
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
        service: RunnerStatusService | None = getattr(self.state, "runner_status", None)
        return service if service is not None else self._refuse("runner status service")

    def trace_status(self) -> LeaseTraceStatusReader:
        reader: LeaseTraceStatusReader | None = getattr(self.state, "trace_status", None)
        return reader if reader is not None else self._refuse("trace status reader")

    def trace_replay(self) -> LeaseTraceReplay:
        replay: LeaseTraceReplay | None = getattr(self.state, "trace_replay", None)
        return replay if replay is not None else self._refuse("trace replay")

    def telemetry_receiver(self) -> TelemetryReceiver:
        receiver: TelemetryReceiver | None = getattr(self.state, "telemetry_receiver", None)
        return receiver if receiver is not None else self._refuse("telemetry receiver")

    def leases(self) -> LocalLeaseService:
        service: LocalLeaseService | None = getattr(self.state, "leases", None)
        return service if service is not None else self._refuse("lease service")

    def transcripts(self) -> TranscriptService:
        service: TranscriptService | None = getattr(self.state, "transcripts", None)
        return service if service is not None else self._refuse("transcript service")

    def takeover(self) -> TakeoverService:
        service: TakeoverService | None = getattr(self.state, "takeover", None)
        return service if service is not None else self._refuse("takeover service")

    def requeue(self) -> RequeueService:
        service: RequeueService | None = getattr(self.state, "requeue", None)
        return service if service is not None else self._refuse("requeue service")

    def attachments(self) -> AttachmentService:
        service: AttachmentService | None = getattr(self.state, "attachments", None)
        return service if service is not None else self._refuse("attachment service")

    def git_commits(self) -> GitCommitDeclarationService:
        service: GitCommitDeclarationService | None = getattr(self.state, "git_commit_declarations", None)
        return service if service is not None else self._refuse("git-commit declaration service")

    def selftests(self) -> SelfTestService:
        service: SelfTestService | None = getattr(self.state, "selftests", None)
        return service if service is not None else self._refuse("selftest service")

    def harnesses(self) -> IHarnessRegistry:
        registry: IHarnessRegistry | None = getattr(self.state, "harnesses", None)
        return registry if registry is not None else self._refuse("harness registry")

    def harness_health(self) -> IReadHarnessHealth:
        """The runner's own health cache, read-only; refuses like every other
        unwired collaborator when the caller wired none."""
        cache: IReadHarnessHealth | None = getattr(self.state, "harness_health", None)
        return cache if cache is not None else self._refuse("harness health cache")

    def asks(self) -> AskService:
        service: AskService | None = getattr(self.state, "asks", None)
        return service if service is not None else self._refuse("ask service")

    def pause(self) -> PauseService:
        service: PauseService | None = getattr(self.state, "pause", None)
        return service if service is not None else self._refuse("pause service")

    def lease_liveness(self) -> LeaseLivenessService:
        service: LeaseLivenessService | None = getattr(self.state, "lease_liveness", None)
        return service if service is not None else self._refuse("lease liveness service")

    def lease_sessions(self) -> LeaseSessionService:
        service: LeaseSessionService | None = getattr(self.state, "lease_sessions", None)
        return service if service is not None else self._refuse("lease session service")

    def workspace_prompts(self) -> WorkspacePromptService:
        service: WorkspacePromptService | None = getattr(self.state, "workspace_prompts", None)
        return service if service is not None else self._refuse("workspace prompt service")

    def events(self) -> IRunnerEventPublisher | None:
        """The publish seam, typed against :mod:`~blizzard.runner.events.publisher`'s Protocol.
        ``None`` when none is wired — never refused."""
        return getattr(self.state, "events", None)

    def maybe_event_broker(self) -> EventBroker | None:
        """The SSE broker the stream route reads; ``None`` when no stream is wired — never refused."""
        return getattr(self.state, "events", None)

    def maybe_shutdown(self) -> asyncio.Event | None:
        """The event ``_lifespan`` sets on shutdown; ``None`` where the app was built without one."""
        return getattr(self.state, "shutdown", None)

    def maybe_readiness(self) -> ReadinessService | None:
        """The readiness service; ``None`` on the store-free app, which reports not ready — never refused."""
        return getattr(self.state, "readiness", None)

    def maybe_config(self) -> RunnerConfig | None:
        return getattr(self.state, "config", None)

    def maybe_read_stores(self) -> RunnerReadStores | None:
        return getattr(self.state, "runner_read_stores", None)

    @staticmethod
    def _refuse(what: str) -> NoReturn:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{what} not wired — start via `blizzard runner host`",
        )
