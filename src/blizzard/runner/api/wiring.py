"""Resolving what the composition root wired onto ``app.state`` (``bzh:dependency-injection``).

Every seam is optional — the OpenAPI exporter and the unit tier build a store-free app — so a
route asks for what it needs and is refused with a ``503`` naming it, never served on nothing.
No accessor here resolves a write-capable store or bundle (``bzh:controller-read-only``):
:meth:`RunnerWiring.read_stores` is the one many-concept read, and every
mutation resolves its own single-concept service instead."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

import httpx
from fastapi import Request, status
from fastapi.exceptions import HTTPException
from starlette.datastructures import State

from blizzard.foundation.clock import IClock, IMonotonicClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing, IPlatformTracing
from blizzard.foundation.platform_tracing.received_export import (
    DisabledReceivedTelemetryExport,
    IReceivedTelemetryExport,
)
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.asks import AskService
from blizzard.runner.domain.attachments import AttachmentService
from blizzard.runner.domain.git_commit_declaration import GitCommitDeclarationService
from blizzard.runner.domain.leases import LeaseRecord, LocalLeaseService
from blizzard.runner.domain.leases.liveness import LeaseLivenessService
from blizzard.runner.domain.leases.session import LeaseSessionService
from blizzard.runner.domain.pause import PauseService
from blizzard.runner.domain.requeue import RequeueService
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.runner.domain.takeover import TakeoverService
from blizzard.runner.domain.tracing.receiver_limits import ReceiverBounds, ReceiverCounter, SpanRateLimiter
from blizzard.runner.domain.tracing.replay import LeaseTraceReplay
from blizzard.runner.domain.tracing.status import LeaseTraceStatusReader
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.health_cache import IReadHarnessHealth
from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.runner.harness.workspace_prompts import WorkspacePromptService
from blizzard.runner.selftest.service import SelfTestService
from blizzard.runner.stores import RunnerReadStores
from blizzard.runner.transcripts.service import TranscriptService

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

    def hub_proxy_client(self) -> httpx.Client:
        client: httpx.Client | None = getattr(self.state, "hub_proxy_client", None)
        return client if client is not None else self._refuse("hub proxy client")

    def hub_retry_clock(self) -> IMonotonicClock:
        clock: IMonotonicClock | None = getattr(self.state, "hub_retry_clock", None)
        return clock if clock is not None else self._refuse("hub proxy retry clock")

    def read_stores(self) -> RunnerReadStores:
        stores = self.maybe_read_stores()
        return stores if stores is not None else self._refuse(_STORE)

    def worker_lease(self, lease_id: str) -> LeaseRecord:
        """The lease a worker verb may act against: the active lease, or — when the ordinary
        active lease is gone — the one an open takeover names. An open takeover
        is a second, independent source of worker-verb authorization, not a re-mint: the
        resolved record's id, node and epoch are unchanged from whatever they already were."""
        stores = self.read_stores()
        lease = stores.lease_record.active_lease(lease_id) or stores.takeover.lease_for_open_takeover(lease_id)
        if lease is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no active lease or open takeover for lease {lease_id}",
            )
        return lease

    def status(self) -> RunnerStatusService:
        service: RunnerStatusService | None = getattr(self.state, "runner_status", None)
        return service if service is not None else self._refuse("runner status service")

    def trace_status(self) -> LeaseTraceStatusReader:
        reader: LeaseTraceStatusReader | None = getattr(self.state, "trace_status", None)
        return reader if reader is not None else self._refuse("trace status reader")

    def trace_replay(self) -> LeaseTraceReplay:
        replay: LeaseTraceReplay | None = getattr(self.state, "trace_replay", None)
        return replay if replay is not None else self._refuse("trace replay")

    def platform_tracing(self) -> IPlatformTracing:
        """The process's platform-tracing handle; the disabled one where the composer wired none."""
        return getattr(self.state, "platform_tracing", None) or DisabledPlatformTracing()

    def span_limiter(self) -> SpanRateLimiter:
        limiter: SpanRateLimiter | None = getattr(self.state, "span_limiter", None)
        return limiter if limiter is not None else self._refuse("span rate limiter")

    def receiver_counter(self) -> ReceiverCounter:
        counter: ReceiverCounter | None = getattr(self.state, "receiver_counter", None)
        return counter if counter is not None else self._refuse("span receiver counter")

    def claude_trace_counter(self) -> ReceiverCounter:
        counter: ReceiverCounter | None = getattr(self.state, "claude_trace_counter", None)
        return counter if counter is not None else self._refuse("Claude Code span receiver counter")

    def metric_bounds(self) -> ReceiverBounds:
        bounds: ReceiverBounds | None = getattr(self.state, "metric_bounds", None)
        return bounds if bounds is not None else self._refuse("metric receiver bounds")

    def log_bounds(self) -> ReceiverBounds:
        bounds: ReceiverBounds | None = getattr(self.state, "log_bounds", None)
        return bounds if bounds is not None else self._refuse("log receiver bounds")

    def received_telemetry(self) -> IReceivedTelemetryExport:
        """The received metrics and logs export; the disabled one where the composer wired none."""
        return getattr(self.state, "received_telemetry", None) or DisabledReceivedTelemetryExport()

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
        """The publish seam — see :mod:`~blizzard.runner.events.publisher`
        for why this is typed against the Protocol, not the concrete broker a composition root
        wires. ``None`` on a composer with no stream to feed — never refused: a mutating route
        publishes when one is wired and is a no-op otherwise, the stream route's own shape."""
        return getattr(self.state, "events", None)

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
