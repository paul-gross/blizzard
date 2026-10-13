"""Host-reported fact intake — a host's own subscription usage samples and misses.

:class:`HostFactIngestService` is the host's store-and-forward push: idempotent against a per-host **high-water mark**
of the host's own, never the runner's. A host's lane carries no chunk, route, or lease, so the runner lane's
route-token gating, chunk fencing, and ``route_ended`` do not reach it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.fact_kinds import EXTERNAL_SUBSCRIPTION_USAGE_MISSED, EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.execution.facts import PushedFact
from blizzard.hub.domain.execution.payload import Payload
from blizzard.hub.domain.execution.usage_facts import (
    external_usage_windows_json,
    subscription_identity,
    usage_miss_reason,
)
from blizzard.hub.domain.runners.host import Host, IWriteHostRegistry

_log = get_logger("blizzard.hub.host_facts")


@domain_model
@dataclass(frozen=True)
class HostFactIngestResult:
    """:meth:`HostFactIngestService.ingest`'s own return — the host's new high-water mark, the pushed seqs partitioned
    into applied, already applied, and rejected for a non-idempotency reason, and the slugs whose usage the push
    freshly wrote."""

    high_water: int
    applied: list[int]
    already_applied: list[int]
    rejected: list[int]
    slugs: frozenset[str]


class HostFactIngestService:
    """Apply a host's batched pushed facts idempotently against its high-water mark."""

    def __init__(self, *, hosts: IWriteHostRegistry, clock: IClock) -> None:
        self._hosts = hosts
        self._clock = clock

    def ingest(self, host: Host, pushed: Sequence[PushedFact]) -> HostFactIngestResult:
        """Apply one push of ``host``'s facts. A retired host raises
        :class:`~blizzard.hub.domain.runners.host.HostRetired` before its high-water mark is read, so nothing lands."""
        host.refuse_if_retired(action="fact ingest")
        mark = self._hosts.high_water(host.host_id)
        applied: list[int] = []
        already: list[int] = []
        rejected: list[int] = []
        slugs: set[str] = set()

        for fact in sorted(pushed, key=lambda f: f.seq):
            if fact.seq <= mark:
                already.append(fact.seq)
                continue
            slug = self._apply(host.host_id, fact.kind, fact.payload)
            if slug is None:
                # A contract mismatch, not an idempotency skip: do not advance the mark past it.
                rejected.append(fact.seq)
                continue
            mark = fact.seq
            applied.append(fact.seq)
            slugs.add(slug)
            # Persisted per applied fact, not once after the loop — bounds a crash mid-batch to the one in-flight
            # fact's re-apply, which the in-place upserts absorb (blizzard-context crash-correctness/hub.md).
            self._hosts.set_high_water(host.host_id, seq=mark, at=self._clock.now())

        _log.info(
            "host facts ingested",
            host_id=host.host_id,
            high_water=mark,
            applied=len(applied),
            already=len(already),
            rejected=len(rejected),
        )
        return HostFactIngestResult(
            high_water=mark, applied=applied, already_applied=already, rejected=rejected, slugs=frozenset(slugs)
        )

    def _apply(self, host_id: str, kind: str, payload: dict[str, object]) -> str | None:
        """Apply one fact; the slug whose usage it wrote, or ``None`` for an unknown kind or a malformed payload."""
        now = self._clock.now()
        fact = Payload(payload)
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED:
            identity = subscription_identity(fact)
            if identity is None:
                return None
            slug, name = identity
            self._hosts.record_external_usage(
                host_id,
                slug=slug,
                name=name,
                sampled_at=fact.instant("sampled_at", now),
                windows_json=external_usage_windows_json(fact.get("windows", []), owner_id=host_id, slug=slug),
                at=now,
            )
            return slug
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_MISSED:
            identity = subscription_identity(fact)
            reason = usage_miss_reason(fact)
            if identity is None or reason is None:
                return None
            slug, name = identity
            self._hosts.record_external_usage_miss(
                host_id, slug=slug, name=name, missed_at=fact.instant("missed_at", now), reason=reason, at=now
            )
            return slug
        _log.warning("unknown host fact kind", kind=kind)
        return None
