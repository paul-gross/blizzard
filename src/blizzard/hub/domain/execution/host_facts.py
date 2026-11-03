"""Host-reported fact intake — a host's own subscription usage samples and misses.

:class:`HostFactIngestService` is the host's store-and-forward push, idempotent against a per-host **high-water
mark**, which each fact's own unit advances by compare-and-set. A host's lane carries no chunk, route, or lease."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

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
from blizzard.hub.domain.kernel.high_water import HighWaterConflict
from blizzard.hub.domain.kernel.transactions import IHubTransactions, Transaction
from blizzard.hub.domain.runners.host import Host, IWriteHostRegistry

_log = get_logger("blizzard.hub.host_facts")


@domain_model
@dataclass(frozen=True)
class _Landing:
    """An admitted host fact's upsert, decided from its payload before its unit opens: ``write`` runs in the unit that
    advances the mark, and ``slug`` names the subscription whose usage it writes."""

    slug: str
    write: Callable[[Transaction], None]


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

    def __init__(self, *, hosts: IWriteHostRegistry, transactions: IHubTransactions, clock: IClock) -> None:
        self._hosts = hosts
        self._transactions = transactions
        self._clock = clock

    def ingest(self, host: Host, pushed: Sequence[PushedFact]) -> HostFactIngestResult:
        """Apply one push of ``host``'s facts. A retired host raises
        :class:`~blizzard.hub.domain.runners.host.HostRetired` before its high-water mark is read, so nothing lands.

        Freshness is decided from the mark this push last read or set. Each fresh fact lands in its own unit with the
        mark's advance, which is conditioned on that seq (``bzh:write-reestablishes-read``): an overlapping push of
        the same host's that moved the mark first rolls the fact back, and this push re-reads the mark and decides
        again from it, so no seq applies twice and the mark never moves back."""
        host.refuse_if_retired(action="fact ingest")
        mark = self._hosts.high_water(host.host_id)
        applied: list[int] = []
        already: list[int] = []
        rejected: list[int] = []
        slugs: set[str] = set()

        for fact in sorted(pushed, key=lambda f: f.seq):
            slug: str | None = None
            while fact.seq > mark:
                try:
                    slug = self._apply(host.host_id, fact, expected=mark)
                    break
                except HighWaterConflict:
                    # Another push of this host's moved the mark since this one read it, and the fact's unit rolled
                    # back with its upsert: decide again against the mark that push left. A mark that did not move
                    # names no such push, and deciding again would only refuse the same way.
                    moved = self._hosts.high_water(host.host_id)
                    if moved == mark:
                        raise
                    mark = moved
            if fact.seq <= mark:
                already.append(fact.seq)
                continue
            if slug is None:
                # A contract mismatch, not an idempotency skip: do not advance the mark past it.
                rejected.append(fact.seq)
                continue
            mark = fact.seq
            applied.append(fact.seq)
            slugs.add(slug)

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

    def _apply(self, host_id: str, fact: PushedFact, *, expected: int) -> str | None:
        """Apply one fact in a unit that advances the mark from ``expected`` to its seq; the slug whose usage it
        wrote, or ``None`` for an unknown kind or a malformed payload, which opens no unit. Raises
        :class:`HighWaterConflict` when the mark has moved from ``expected``, landing nothing."""
        now = self._clock.now()
        landing = self._landing(host_id, fact.kind, Payload(fact.payload), now=now)
        if landing is None:
            return None
        with self._transactions.begin() as unit:
            landing.write(unit)
            self._hosts.set_high_water(host_id, seq=fact.seq, expected=expected, at=now, transaction=unit)
        return landing.slug

    def _landing(self, host_id: str, kind: str, fact: Payload, *, now: datetime) -> _Landing | None:
        """A subscription's sample or miss, each refreshed in place per ``(host_id, slug)`` in its own table; ``None``
        for an unknown kind or a malformed payload."""
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED:
            identity = subscription_identity(fact)
            if identity is None:
                return None
            slug, name = identity
            sampled_at = fact.instant("sampled_at", now)
            windows_json = external_usage_windows_json(fact.get("windows", []), owner_id=host_id, slug=slug)
            return _Landing(
                slug=slug,
                write=lambda transaction: self._hosts.record_external_usage(
                    host_id,
                    slug=slug,
                    name=name,
                    sampled_at=sampled_at,
                    windows_json=windows_json,
                    at=now,
                    transaction=transaction,
                ),
            )
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_MISSED:
            identity = subscription_identity(fact)
            reason = usage_miss_reason(fact)
            if identity is None or reason is None:
                return None
            slug, name = identity
            missed_at = fact.instant("missed_at", now)
            return _Landing(
                slug=slug,
                write=lambda transaction: self._hosts.record_external_usage_miss(
                    host_id,
                    slug=slug,
                    name=name,
                    missed_at=missed_at,
                    reason=reason,
                    at=now,
                    transaction=transaction,
                ),
            )
        _log.warning("unknown host fact kind", kind=kind)
        return None
