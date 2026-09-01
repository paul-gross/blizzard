"""Resolving what the composition root wired onto ``app.state`` (``bzh:dependency-injection``).

Every seam is optional — the OpenAPI exporter and the unit tier build a store-free app — so a
route asks for what it needs and is refused with a ``503`` naming it, never served on nothing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from fastapi import Request, status
from fastapi.exceptions import HTTPException
from starlette.datastructures import State

from blizzard.foundation.clock import IClock
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.attachments import AttachmentService
from blizzard.runner.domain.git_commit_declaration import GitCommitDeclarationService
from blizzard.runner.domain.leases import LeaseRecord, LocalLeaseService
from blizzard.runner.domain.requeue import RequeueService
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.runner.domain.takeover import TakeoverService
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.selftest.service import SelfTestService
from blizzard.runner.stores import RunnerStores
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

    def stores(self) -> RunnerStores:
        stores = self.maybe_stores()
        return stores if stores is not None else self._refuse(_STORE)

    def worker_lease(self, lease_id: str) -> LeaseRecord:
        """The lease a worker verb may act against: the active lease, or — when the ordinary
        active lease is gone — the one an open takeover names (issue #291). An open takeover
        is a second, independent source of worker-verb authorization, not a re-mint: the
        resolved record's id, node and epoch are unchanged from whatever they already were."""
        stores = self.stores()
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

    def events(self) -> IRunnerEventPublisher | None:
        """The publish seam (D2/D4, blizzard#317) — see :mod:`~blizzard.runner.events.publisher`
        for why this is typed against the Protocol, not the concrete broker a composition root
        wires. ``None`` on a composer with no stream to feed — never refused: a mutating route
        publishes when one is wired and is a no-op otherwise, the stream route's own shape."""
        return getattr(self.state, "events", None)

    def maybe_config(self) -> RunnerConfig | None:
        return getattr(self.state, "config", None)

    def maybe_stores(self) -> RunnerStores | None:
        return getattr(self.state, "runner_stores", None)

    @staticmethod
    def _refuse(what: str) -> NoReturn:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{what} not wired — start via `blizzard runner host`",
        )
