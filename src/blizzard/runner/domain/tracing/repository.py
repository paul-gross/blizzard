"""The lease trace-facts read seam — closed leases only, singular and plural."""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol

from blizzard.runner.domain.tracing.facts import LeaseTraceFacts


class IReadLeaseTraceFacts(Protocol):
    def lease_trace_facts(self, lease_id: str) -> LeaseTraceFacts | None:
        """The lease's facts, or ``None`` when it is unknown or has no closure yet."""
        ...

    def lease_trace_facts_for(self, lease_ids: Collection[str]) -> dict[str, LeaseTraceFacts]:
        """Each closed lease's facts keyed by lease id, in a fixed number of statements per batch;
        an unknown or unclosed id is absent."""
        ...
