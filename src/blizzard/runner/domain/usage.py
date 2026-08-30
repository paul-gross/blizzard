"""The token-usage, context-sample, and external-subscription-usage repository seam
(blizzard#410)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.runner.harness.usage import UsageSample

__all__ = [
    "ContextSampleState",
    "IReadUsageRepository",
    "IWriteUsageRepository",
    "UsageTotals",
]


@dataclass(frozen=True)
class ContextSampleState:
    """What a lease's recorded context samples establish so far — the sampler's own memory."""

    #: The newest sample's stamp: the cadence anchor, derived rather than a stored column.
    last_sampled_at: datetime
    #: The highest context measured, or ``None`` when no attempt measured one — the warn dedupe.
    max_context_tokens: int | None


@dataclass(frozen=True)
class UsageTotals:
    """A summed window of usage facts (issue #58). ``cost_partial`` carries the
    lower-bound contract on ``cost_usd``: a caller must check it before treating
    ``cost_usd`` as exact."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


class IReadUsageRepository(Protocol):
    """Read-only usage/context-sample queries (held by read-path edges)."""

    def usage_since(self, at: datetime) -> UsageTotals:
        """Sum every local usage fact recorded at or after ``at`` (issue #58) — see
        :class:`UsageTotals` for the lower-bound + PARTIAL contract on ``cost_usd``."""
        ...

    def context_sample_state(self, lease_id: str) -> ContextSampleState | None:
        """What this lease's context samples already establish, or ``None`` if none exist.

        One read answering both of the sampler's questions — when it last sampled (the
        cadence anchor) and the highest context it has seen (whether the warn line has
        already been crossed, so the warning fires once rather than every sample)."""
        ...

    def last_external_usage_attempt_at(self) -> datetime | None:
        """The derived cadence anchor for the external-subscription-usage sample step
        (issue #218): ``max(sampled_at)`` across ``external_usage_samples``, or ``None``.

        Derived, never a stored column (``bzh:facts-not-status``). A NULL-``payload``
        attempt counts exactly like a successful one — this runner *tried* then."""
        ...


class IWriteUsageRepository(IReadUsageRepository, Protocol):
    """Read-write usage/context-sample store — held only by the domain."""

    def record_usage(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        sample: UsageSample,
        recorded_at: datetime,
    ) -> int | None:
        """Idempotently record one usage fact **and** buffer its outbound report,
        atomically (issue #58); return the buffered report's seq. Keyed on
        ``(lease_id, generation, sample.kind)``: a resume within the same lease is a
        genuinely new row; an exact replay writes nothing, buffers nothing, returns ``None``."""
        ...

    def record_context_sample(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        session_id: str,
        context_tokens: int | None,
        sampled_at: datetime,
        report_kind: str = "",
        report_payload: str = "",
    ) -> int | None:
        """Append one context-sample attempt and, when a report is given, buffer it and
        return its seq, atomically. ``context_tokens is None`` records an attempt that
        measured nothing, which still advances the cadence anchor. An empty
        ``report_kind`` records the sample alone — the ordinary case, since only a
        first crossing reports — and returns ``None``, no report buffered."""
        ...

    def record_external_usage_attempt(
        self, *, sampled_at: datetime, payload: str | None, report_kind: str, report_payload: str
    ) -> int | None:
        """Append one external-subscription-usage sampling attempt **and**, only when it
        produced a sample, buffer its outbound report — atomically (issue #218). The
        attempt row is always appended, whether or not the harness had anything to
        report; the outbound fact exists only when ``payload`` is not ``None``, its seq
        returned then and ``None`` otherwise."""
        ...
