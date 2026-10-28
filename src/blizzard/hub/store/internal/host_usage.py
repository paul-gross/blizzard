"""The host-keyed reads of a host's reported subscription usage and roster (package-private).

Two adapters read them — the host registry for the operator's host view, the runner registry for a hosted runner's
registration — so the statements live here once, one query per id batch (``bzh:bulk-reconstitution``)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.runners.host import HostUsage
from blizzard.hub.domain.runners.registration import (
    DeclaredSubscription,
    ExternalSubscriptionUsageWindow,
    SubscriptionUsageMiss,
    SubscriptionUsageSample,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import ScopedConnections


def stored_usage_windows(windows_json: str) -> tuple[ExternalSubscriptionUsageWindow, ...]:
    """Valid windows from current or pre-validation stored samples."""
    try:
        entries = json.loads(windows_json)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(entries, list):
        return ()
    items = cast(list[Any], entries)
    admitted = (ExternalSubscriptionUsageWindow.admitted(entry) for entry in items)
    return tuple(window for window in admitted if not isinstance(window, str))


def read_host_usage(store: ScopedConnections, conn: Connection, host_ids: Sequence[str]) -> dict[str, HostUsage]:
    """Each listed host's newest sample and miss per slug, one entry per id."""
    samples: dict[str, list[SubscriptionUsageSample]] = {host_id: [] for host_id in host_ids}
    misses: dict[str, list[SubscriptionUsageMiss]] = {host_id: [] for host_id in host_ids}
    usage, missed = s.host_external_usage, s.host_external_usage_misses
    for batch in id_batches(list(host_ids)):
        for row in conn.execute(
            store.select(usage).where(usage.c.host_id.in_(batch)).order_by(usage.c.host_id, usage.c.slug)
        ):
            samples[row.host_id].append(
                SubscriptionUsageSample(
                    slug=row.slug, name=row.name, sampled_at=row.sampled_at, windows=stored_usage_windows(row.windows)
                )
            )
        for row in conn.execute(
            store.select(missed).where(missed.c.host_id.in_(batch)).order_by(missed.c.host_id, missed.c.slug)
        ):
            misses[row.host_id].append(
                SubscriptionUsageMiss(slug=row.slug, name=row.name, missed_at=row.missed_at, reason=row.reason)
            )
    return {host_id: HostUsage(tuple(samples[host_id]), tuple(misses[host_id])) for host_id in host_ids}


def read_host_rosters(
    store: ScopedConnections, conn: Connection, host_ids: Sequence[str]
) -> dict[str, tuple[DeclaredSubscription, ...]]:
    """Each listed host's last-reported subscription roster; ``()`` for a host that reported none."""
    rosters: dict[str, tuple[DeclaredSubscription, ...]] = dict.fromkeys(host_ids, ())
    hosts = s.hosts
    for batch in id_batches(list(host_ids)):
        for row in conn.execute(
            store.select(hosts, hosts.c.host_id, hosts.c.subscriptions).where(hosts.c.host_id.in_(batch))
        ):
            declared: list[dict[str, Any]] = json.loads(row.subscriptions) if row.subscriptions else []
            rosters[row.host_id] = tuple(
                DeclaredSubscription(slug=d["slug"], name=d["name"], provider=d["provider"]) for d in declared
            )
    return rosters
