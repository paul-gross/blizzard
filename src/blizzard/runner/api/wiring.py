"""Resolving what the composition root wired onto ``app.state`` (``bzh:dependency-injection``).

Every seam is optional — the OpenAPI exporter and the unit tier build a store-free app — so a
route asks for what it needs and is refused with a ``503`` naming it, never served on nothing.
No accessor here resolves a write-capable store or bundle (``bzh:controller-read-only``,
blizzard#412): :meth:`RunnerWiring.read_stores` is the one many-concept read, and every
mutation resolves its own single-concept service instead."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn

import httpx
from fastapi import Request, status
from fastapi.exceptions import HTTPException
from starlette.datastructures import State

from blizzard.foundation.clock import IClock
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
from blizzard.runner.events.publisher import IRunnerEventPublisher
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

    def hub_retry_delay(self) -> Callable[[float], None]:
        delay: Callable[[float], None] | None = getattr(self.state, "hub_retry_delay", None)
        return delay if delay is not None else self._refuse("hub proxy retry delay")

    def read_stores(self) -> RunnerReadStores:
        stores = self.maybe_read_stores()
        return stores if stores is not None else self._refuse(_STORE)

    def worker_lease(self, lease_id: str) -> LeaseRecord:
        """The lease a worker verb may act against: the active lease, or — when the ordinary
        active lease is gone — the one an open takeover names (issue #291). An open takeover
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
        """The publish seam (D2/D4, blizzard#317) — see :mod:`~blizzard.runner.events.publisher`
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
