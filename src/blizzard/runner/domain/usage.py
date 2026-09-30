"""The token-usage, context-sample, and external-subscription-usage repository seam."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.usage import SessionCostBasis, UsageSample, invocation_cost
from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageWindow

__all__ = [
    "ContextSampleState",
    "ExternalUsageAttemptSummary",
    "IReadUsageRepository",
    "IWriteUsageRepository",
    "InvocationCost",
    "UsageTotals",
    "derive_invocation_cost",
]


@dataclass(frozen=True)
class ContextSampleState:
    """What a lease's recorded context samples establish so far — the sampler's own memory."""

    #: The newest sample's stamp: the cadence anchor, derived rather than a stored column.
    last_sampled_at: datetime
    #: The highest context measured, or ``None`` when no attempt measured one — the warn dedupe.
    max_context_tokens: int | None


@dataclass(frozen=True)
class ExternalUsageAttemptSummary:
    """This ``slug``'s own newest sampling attempt — what the probe, ``runner
    status``, and ``GET /api/subscriptions`` all show. ``miss_reason`` is a
    :class:`~blizzard.runner.subscriptions.subscription_sampler.SampleMissReason` value or
    ``None`` on success; ``renewal`` is the renewal outcome recorded with it, or ``None``."""

    slug: str
    sampled_at: datetime
    ok: bool
    miss_reason: str | None
    renewal: str | None


@dataclass(frozen=True)
class InvocationCost:
    """The two cost figures one usage fact persists, decided by :func:`derive_invocation_cost`."""

    #: This invocation's own billed share; ``None`` is cost unknown.
    cost_usd: float | None
    #: The zero-cost steps' estimate; ``None`` when absent or withheld with a rejected billed reading.
    estimated_cost_usd: float | None


def derive_invocation_cost(sample: UsageSample, basis: SessionCostBasis | None) -> InvocationCost:
    """The figures to persist for ``sample`` read against its session's banked ``basis``.

    A billed reading :func:`~blizzard.runner.harness.usage.invocation_cost` rejects withholds
    the estimate too, so the two readings can never diverge."""
    cost_usd = invocation_cost(sample, basis)
    billed_reading_rejected = cost_usd is None and sample.cost_usd is not None
    return InvocationCost(
        cost_usd=cost_usd,
        estimated_cost_usd=None if billed_reading_rejected else sample.estimated_cost_usd,
    )


@dataclass(frozen=True)
class UsageTotals:
    """A summed window of usage facts. ``cost_partial`` carries the
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

    def session_cost_basis(self, lease_id: str) -> SessionCostBasis | None:
        """What ``lease_id``'s session has banked; ``None`` without an identified session.

        Keyed on the session, not the lease, because a session outlives the lease it was minted
        under. The basis holds still only because one session is driven by one lease at a
        time — no transaction serializes this read against :meth:`~IWriteUsageRepository.record_usage`'s
        insert."""
        ...

    def usage_since(self, at: datetime) -> UsageTotals:
        """Sum every local usage fact recorded at or after ``at`` — see
        :class:`UsageTotals` for the lower-bound + PARTIAL contract on ``cost_usd``."""
        ...

    def context_sample_state(self, lease_id: str) -> ContextSampleState | None:
        """What this lease's context samples already establish, or ``None`` if none exist.

        One read answering both of the sampler's questions — when it last sampled (the
        cadence anchor) and the highest context it has seen (whether the warn line has
        already been crossed, so the warning fires once rather than every sample)."""
        ...

    def context_sample_states(self, lease_ids: Sequence[str]) -> dict[str, ContextSampleState]:
        """:meth:`context_sample_state` for every id in ``lease_ids``, in one grouped read
        (`bzh:bulk-reconstitution`) — the caller filters "due" itself from the returned
        state. A lease with no samples yet is absent, exactly as the singular getter
        answers ``None`` for it."""
        ...

    def last_external_usage_attempt_at(self, slug: str) -> datetime | None:
        """``max(sampled_at)`` across this ``slug``'s own rows, or ``None``. A
        NULL-``payload`` attempt counts like a successful one, so one subscription's
        failed sample never masks its own windows."""
        ...

    def latest_external_usage_windows(self, slug: str) -> tuple[ExternalSubscriptionUsageWindow, ...]:
        """This ``slug``'s own newest sampled snapshot's windows, decoded from the stored
        payload — empty when never sampled, or when the newest attempt recorded none.
        The usage-limit reset-time fallback's own read: no
        harness-to-subscription mapping, just the newest windows this slug reported."""
        ...

    def latest_external_usage_windows_by_slug(
        self, slugs: Sequence[str]
    ) -> dict[str, tuple[ExternalSubscriptionUsageWindow, ...]]:
        """:meth:`latest_external_usage_windows` for every slug in ``slugs``, in one batched read
        (`bzh:bulk-reconstitution`) — each slug's answer exactly the singular's, including its
        exclusion of failed-attempt rows before the newest is picked. A slug that answers no
        windows is absent, which the caller reads as ``()``."""
        ...

    def latest_external_usage_attempt(self, slug: str) -> ExternalUsageAttemptSummary | None:
        """This ``slug``'s own newest attempt row, or ``None`` when never attempted —
        the runner-local diagnostics' read."""
        ...

    def latest_external_usage_attempts_by_slug(self, slugs: Sequence[str]) -> dict[str, ExternalUsageAttemptSummary]:
        """:meth:`latest_external_usage_attempt` for every slug in ``slugs``, in one batched read
        (`bzh:bulk-reconstitution`). A slug never attempted is absent, which the caller reads as ``None``."""
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
        cost: InvocationCost,
        recorded_at: datetime,
    ) -> int | None:
        """Idempotently record one usage fact **and** buffer its outbound report, atomically;
        return the buffered report's seq. Keyed on ``(lease_id, generation,
        sample.kind)``: a resume within the same lease is a genuinely new row; an exact replay
        writes nothing, buffers nothing, returns ``None``. The figures stored and reported are
        exactly the ``cost`` handed in — the store decides none of them."""
        ...

    def record_context_sample(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        context_tokens: int | None,
        sampled_at: datetime,
        session: SessionReference,
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
        self,
        *,
        slug: str,
        sampled_at: datetime,
        payload: str | None,
        report_kind: str,
        report_payload: str,
        miss_reason: str | None = None,
        renewal: str | None = None,
    ) -> int | None:
        """Append one declared subscription's sampling attempt and, when it carries a report,
        buffer that report — atomically, returning the buffered seq or ``None``.
        ``slug`` is the cadence's join key. ``miss_reason`` is a
        ``SampleMissReason`` value on a miss, ``None`` on success; ``renewal`` is this attempt's
        own renewal outcome, ``None`` when this slug has no renewer or none was due."""
        ...

    def prune_external_usage_samples(self, *, now: datetime) -> int:
        """Compact external-usage-sample attempts older than the store's own retention
        window, keeping each slug's newest attempt regardless of age —
        ``max(sampled_at)`` per slug is unchanged, so
        :meth:`~IReadUsageRepository.last_external_usage_attempt_at` answers identically
        before and after. Returns the number of rows pruned."""
        ...
