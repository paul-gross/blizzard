"""RETENTION — prune every append-only observation/report lane once per floor, so none grows without bound."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.outbound_buffer import IWriteOutboundRepository
from blizzard.runner.leases.liveness import IWriteLeaseLivenessRepository
from blizzard.runner.leases.worker_stdout import IWorkerStdoutFiles
from blizzard.runner.loop.retention_floor import RetentionPasses
from blizzard.runner.throttle.usage_limits import (
    IWriteRunnerUsageLimitRepository,
    IWriteSubscriptionUsageLimitRepository,
)
from blizzard.runner.usage.repository import IWriteExternalUsageRepository

_log = get_logger("blizzard.runner.loop")


class RetentionConfig(Protocol):
    @property
    def worker_stdout_retention_days(self) -> int: ...


class RetentionStores(Protocol):
    @property
    def outbound(self) -> IWriteOutboundRepository: ...
    @property
    def liveness(self) -> IWriteLeaseLivenessRepository: ...
    @property
    def usage_limits(self) -> IWriteRunnerUsageLimitRepository: ...


class RetentionHostStores(Protocol):
    @property
    def external_usage(self) -> IWriteExternalUsageRepository: ...
    @property
    def usage_limits(self) -> IWriteSubscriptionUsageLimitRepository: ...


class RetentionContext(Protocol):
    @property
    def config(self) -> RetentionConfig: ...
    @property
    def stores(self) -> RetentionStores: ...
    @property
    def host_stores(self) -> RetentionHostStores: ...
    @property
    def worker_files(self) -> IWorkerStdoutFiles: ...
    @property
    def retention_passes(self) -> RetentionPasses | None: ...
    @property
    def clock(self) -> IClock: ...


@dataclass(frozen=True)
class RetentionPrune:
    """Each lane's own retention contract lives at its own method (the store's ``prune_outbound`` and its siblings,
    or the filesystem sweep of ``IWorkerStdoutFiles.sweep``). Gated by ``RetentionPasses.due`` —
    ``bzh:probe-gated-pass``'s floor-only form."""

    ctx: RetentionContext

    def run(self) -> None:
        """One prune per lane, each isolated: one lane's failure must not skip the others, and this step gates
        nothing else in the tick either way."""
        ctx = self.ctx
        now = ctx.clock.now()
        passes = ctx.retention_passes
        if passes is not None and not passes.due(now):
            return
        lanes: tuple[tuple[str, Callable[[], int]], ...] = (
            ("outbound buffer", lambda: ctx.stores.outbound.prune_outbound(now=now)),
            ("heartbeat", lambda: ctx.stores.liveness.prune_heartbeats(now=now)),
            ("external usage sample", lambda: ctx.host_stores.external_usage.prune_external_usage_samples(now=now)),
            ("credential renewal", lambda: ctx.host_stores.external_usage.prune_credential_renewals(now=now)),
            ("subscription usage limit", lambda: ctx.host_stores.usage_limits.prune_usage_limits(now=now)),
            ("runner usage limit", lambda: ctx.stores.usage_limits.prune_usage_limits(now=now)),
            (
                "worker stdout",
                lambda: ctx.worker_files.sweep(
                    now=now, retention=timedelta(days=ctx.config.worker_stdout_retention_days)
                ),
            ),
        )
        for label, prune in lanes:
            try:
                prune()
            except Exception as exc:  # one lane's prune failure must not skip the others
                _log.warning("retention prune failed", lane=label, detail=str(exc))
        # Every lane was attempted, whatever its outcome: a lane that raised retries at the next floor.
        if passes is not None:
            passes.record(now)
