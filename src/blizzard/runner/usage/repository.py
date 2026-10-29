"""The token-usage, context-sample, and external-subscription-usage repository seam."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.foundation.credential_renewal import RenewalFailureReason, RenewalResult
from blizzard.foundation.fact_kinds import EXTERNAL_SUBSCRIPTION_USAGE_MISSED, EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED
from blizzard.foundation.roles import domain_model
from blizzard.foundation.spend import SpendFigure
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.usage import SessionCostBasis, UsageKind, UsageSample, invocation_cost
from blizzard.runner.subscriptions.credential_renewer import RenewalOutcome
from blizzard.runner.subscriptions.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ExternalSubscriptionUsageWindow,
    SampleMiss,
)

__all__ = [
    "ChargeRange",
    "ContextSampleState",
    "CredentialRenewalSummary",
    "ExternalUsageAttempt",
    "ExternalUsageAttemptSummary",
    "ExternalUsageSampleSummary",
    "HostBufferedFact",
    "IReadCredentialRenewalRepository",
    "IReadExternalUsageRepository",
    "IReadHostLaneRepository",
    "IReadUsageRepository",
    "IWriteCredentialRenewalRepository",
    "IWriteExternalUsageAttemptRepository",
    "IWriteExternalUsageRepository",
    "IWriteHostLaneAppendRepository",
    "IWriteHostLaneMarkRepository",
    "IWriteHostLaneRepository",
    "IWriteUsageRepository",
    "InvocationCost",
    "UsageFactRecord",
    "UsageTotals",
    "charge_range",
    "derive_invocation_cost",
    "effective_model",
    "external_usage_attempt",
    "usage_kind_for",
]


@domain_model
@dataclass(frozen=True)
class ContextSampleState:
    """What a lease's recorded context samples establish so far — the sampler's own memory."""

    #: The newest sample's stamp: the cadence anchor, derived rather than a stored column.
    last_sampled_at: datetime
    #: The highest context measured, or ``None`` when no attempt measured one — the warn dedupe.
    max_context_tokens: int | None

    @classmethod
    def sample_due(cls, state: ContextSampleState | None, *, now: datetime, interval: timedelta) -> bool:
        """A lease samples once ``interval`` has passed since its newest sample, or at once
        when it was never sampled."""
        return state is None or now - state.last_sampled_at >= interval

    @classmethod
    def first_crossing(cls, state: ContextSampleState | None, *, tokens: int | None, warn_tokens: int) -> bool:
        """Only the first sample past the warn line reports, once per lease: the warning is a
        state change, not a level, so a lease already past it samples on without re-reporting."""
        if tokens is None or tokens <= warn_tokens:
            return False
        return not (state is not None and (state.max_context_tokens or 0) > warn_tokens)


@domain_model
@dataclass(frozen=True)
class ExternalUsageAttemptSummary:
    """This ``slug``'s own newest sampling attempt. ``miss_reason`` is a
    :class:`~blizzard.runner.subscriptions.subscription_sampler.SampleMissReason` value or
    ``None`` on success."""

    slug: str
    sampled_at: datetime
    ok: bool
    miss_reason: str | None


@domain_model
@dataclass(frozen=True)
class ExternalUsageSampleSummary:
    """This ``slug``'s own newest sampled snapshot: when it was sampled, and its windows."""

    slug: str
    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]


@domain_model
@dataclass(frozen=True)
class CredentialRenewalSummary:
    """This ``slug``'s own newest credential renewal, for the runner-local diagnostics.
    ``attempted_at`` is its claim, just before it fired; ``result`` is ``UNRECORDED`` while that
    claim has no outcome on record; ``failure_reason`` is set only on a failed one."""

    slug: str
    attempted_at: datetime
    result: RenewalResult
    failure_reason: RenewalFailureReason | None


@domain_model
@dataclass(frozen=True)
class InvocationCost:
    """The two cost figures one usage fact persists, decided by :func:`derive_invocation_cost`."""

    #: This invocation's own billed share; ``None`` is cost unknown.
    cost_usd: float | None
    #: The zero-cost steps' estimate; ``None`` when absent or withheld with a rejected billed reading.
    estimated_cost_usd: float | None
    #: The harness reported a billed figure the session's banked basis rejects — worth a warning.
    billed_reading_rejected: bool = field(default=False, compare=False)


def derive_invocation_cost(sample: UsageSample, basis: SessionCostBasis | None) -> InvocationCost:
    """The figures to persist for ``sample`` read against its session's banked ``basis``.

    A billed reading :func:`~blizzard.runner.harness.usage.invocation_cost` rejects withholds
    the estimate too, so the two readings can never diverge."""
    cost_usd = invocation_cost(sample, basis)
    billed_reading_rejected = cost_usd is None and sample.cost_usd is not None
    return InvocationCost(
        cost_usd=cost_usd,
        estimated_cost_usd=None if billed_reading_rejected else sample.estimated_cost_usd,
        billed_reading_rejected=billed_reading_rejected,
    )


def usage_kind_for(generation: int) -> UsageKind:
    """A lease's first generation is its spawn; every later one is a resume."""
    return "spawn" if generation <= 1 else "resume"


def effective_model(requested: str | None, observed: str | None) -> str | None:
    """The model a usage fact prices against: the lease's requested model wins, else what the
    transcript observed ran."""
    return requested if requested is not None else observed


class _RangeStart(Protocol):
    """A recorded invocation start, as either boundary read answers it."""

    @property
    def start_position(self) -> str | None: ...
    @property
    def start_unreadable(self) -> bool: ...


@domain_model
@dataclass(frozen=True)
class ChargeRange:
    """The transcript range one invocation is charged for: from ``start`` (``None`` is the
    transcript's beginning) to ``end`` (``None`` is the tail as it stands now)."""

    start: str | None
    end: str | None


def charge_range(start: _RangeStart | None, end: _RangeStart | None) -> ChargeRange | None:
    """The range an invocation is charged for, or ``None`` to charge nothing. No recorded start
    charges nothing, so a whole session is never charged to one generation; an unreadable start
    charges nothing. A later invocation's recorded start caps the range; an unreadable
    one charges nothing, and none at all reads to the tail. ``tests/test_runner_usage_rules.py`` pins both."""
    if start is None or start.start_unreadable:
        return None
    if end is not None and end.start_unreadable:
        return None
    return ChargeRange(start=start.start_position, end=end.start_position if end is not None else None)


@domain_model
@dataclass(frozen=True)
class UsageFactRecord:
    """One recorded usage fact as the runner stored it — an invocation's own figures, never a sum."""

    kind: str
    generation: int
    node_id: str
    epoch: int
    model: str
    harness_id: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float | None
    estimated_cost_usd: float | None
    recorded_at: datetime


@domain_model
@dataclass(frozen=True)
class UsageTotals:
    """A summed window of usage facts, its cost read as the one spend figure. The PARTIAL reading is
    the hub's ``UsageTotal``'s (``src/blizzard/hub/domain/chunk/model.py``): ``estimated_cost_usd`` is
    ``None`` when no summed row carried one, and ``cost_partial`` marks a row carrying neither amount."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool
    estimated_cost_usd: float | None = None

    @property
    def figure(self) -> SpendFigure:
        return SpendFigure(self.cost_usd, self.estimated_cost_usd, self.cost_partial)

    @property
    def spend_usd(self) -> float:
        """Billed plus estimated — the figure's amount."""
        return self.figure.amount()

    def reaches(self, cap: float) -> bool:
        """The summed spend is at or past ``cap`` — a lower bound when :attr:`cost_partial`."""
        return self.spend_usd >= cap


@domain_model
@dataclass(frozen=True)
class ExternalUsageAttempt:
    """One declared subscription's sampling attempt to record: a miss with its reason, or a
    sampled snapshot whose windows the stored payload carries."""

    slug: str
    sampled_at: datetime
    snapshot: ExternalSubscriptionUsageSnapshot | None
    miss_reason: SampleMissReason | None

    @property
    def missed(self) -> bool:
        return self._missed()

    def _missed(self) -> bool:
        return self.snapshot is None

    @property
    def result(self) -> ExternalSubscriptionUsageSnapshot | SampleMissReason:
        """The sampled snapshot, or — on a miss — the reason it missed."""
        return self._result()

    def _result(self) -> ExternalSubscriptionUsageSnapshot | SampleMissReason:
        if self.snapshot is not None:
            return self.snapshot
        assert self.miss_reason is not None  # external_usage_attempt pairs every miss with its reason
        return self.miss_reason

    @property
    def report_kind(self) -> str:
        return self._report_kind()

    def _report_kind(self) -> str:
        return EXTERNAL_SUBSCRIPTION_USAGE_MISSED if self.missed else EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED


def external_usage_attempt(
    result: ExternalSubscriptionUsageSnapshot | SampleMiss, *, slug: str, at: datetime
) -> ExternalUsageAttempt:
    """The attempt row one sampler result records. A miss is still an attempt: its slug's
    cadence advances, it stores no payload, and its reason feeds the runner-local diagnostics."""
    if isinstance(result, SampleMiss):
        return ExternalUsageAttempt(slug=slug, sampled_at=at, snapshot=None, miss_reason=result.reason)
    return ExternalUsageAttempt(slug=slug, sampled_at=at, snapshot=result, miss_reason=None)


class IReadCredentialRenewalRepository(Protocol):
    """Read-only credential-renewal queries: the renewal cadence's anchor and the newest
    renewal per slug for the runner-local diagnostics."""

    def last_credential_renewal_claim_at(self, slug: str) -> datetime | None:
        """``max(claimed_at)`` across this ``slug``'s own renewal claims, or ``None`` — the
        renewal cadence's anchor. A claim counts whether or not its outcome was recorded."""
        ...

    def latest_credential_renewals_by_slug(self, slugs: Sequence[str]) -> dict[str, CredentialRenewalSummary]:
        """Each slug's newest renewal claim with its outcome, if one is recorded, in one batched
        read (`bzh:bulk-reconstitution`). A slug never renewed is absent, which the caller reads
        as ``None``."""
        ...


class IWriteCredentialRenewalRepository(IReadCredentialRenewalRepository, Protocol):
    """Read-write credential-renewal facts — held only by the renewal pass and retention.
    The claim and the outcome are separate immutable facts: a claim is never updated."""

    def claim_credential_renewal(self, *, slug: str, claimed_at: datetime) -> int:
        """Durably record that a renewal of ``slug`` is about to fire, returning the claim's id.
        Committed before the vendor CLI is invoked, so a lost outcome can never permit a
        repeat renewal inside the cadence."""
        ...

    def record_credential_renewal_outcome(
        self, *, claim_id: int, outcome: RenewalOutcome, recorded_at: datetime
    ) -> None:
        """Record the outcome of the renewal ``claim_id`` claimed — at most once per claim."""
        ...

    def prune_credential_renewals(self, *, now: datetime) -> int:
        """Compact renewal claims older than the store's own retention window, with their
        outcomes, keeping each slug's newest claim regardless of age — ``max(claimed_at)``
        per slug is unchanged, so :meth:`~IReadCredentialRenewalRepository.last_credential_renewal_claim_at`
        answers identically before and after. Returns the number of claims pruned."""
        ...


class IReadUsageRepository(Protocol):
    """Read-only usage/context-sample queries."""

    def session_cost_basis(self, lease_id: str) -> SessionCostBasis | None:
        """What ``lease_id``'s session has banked; ``None`` without an identified session.

        Keyed on the session, not the lease, because a session outlives the lease it was minted
        under. The basis holds still only because one session is driven by one lease at a
        time — no transaction serializes this read against :meth:`~IWriteUsageRepository.record_usage`'s
        insert."""
        ...

    def usage_since(self, at: datetime) -> UsageTotals:
        """Sum every local usage fact recorded at or after ``at`` — see
        :class:`UsageTotals` for the PARTIAL reading of its figure."""
        ...

    def lease_usage_facts(self, lease_id: str) -> list[UsageFactRecord]:
        """Every usage fact recorded for ``lease_id``, oldest first — bounded by the lease's
        own invocations. An unknown lease and a lease with none read alike: empty."""
        ...

    def context_sample_state(self, lease_id: str) -> ContextSampleState | None:
        """What this lease's context samples already establish, or ``None`` if none exist.

        One read answering both — when the lease was last sampled and the highest context
        sampled for it."""
        ...

    def context_sample_states(self, lease_ids: Sequence[str]) -> dict[str, ContextSampleState]:
        """:meth:`context_sample_state` for every id in ``lease_ids``, in one grouped read
        (`bzh:bulk-reconstitution`) — the caller filters "due" itself from the returned
        state. A lease with no samples yet is absent, exactly as the singular getter
        answers ``None`` for it."""
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


class IReadExternalUsageRepository(IReadCredentialRenewalRepository, Protocol):
    """Read-only external-usage-sample queries: each declared subscription's sampling attempts — host-scoped, a fact
    about the account rather than the runner, reached through :class:`~blizzard.runner.stores.HostStores`."""

    def last_external_usage_attempt_at(self, slug: str) -> datetime | None:
        """``max(sampled_at)`` across this ``slug``'s own rows, or ``None``. A
        NULL-``payload`` attempt counts like a successful one, so one subscription's
        failed sample never masks its own windows."""
        ...

    def latest_external_usage_windows(self, slug: str) -> tuple[ExternalSubscriptionUsageWindow, ...]:
        """This ``slug``'s own newest sampled snapshot's windows, decoded from the stored
        payload — empty when never sampled, or when the newest attempt recorded none."""
        ...

    def latest_external_usage_windows_by_slug(
        self, slugs: Sequence[str]
    ) -> dict[str, tuple[ExternalSubscriptionUsageWindow, ...]]:
        """:meth:`latest_external_usage_windows` for every slug in ``slugs``, in one batched read
        (`bzh:bulk-reconstitution`) — each slug's answer exactly the singular's, including its
        exclusion of failed-attempt rows before the newest is picked. A slug that answers no
        windows is absent, which the caller reads as ``()``."""
        ...

    def latest_external_usage_samples_by_slug(self, slugs: Sequence[str]) -> dict[str, ExternalUsageSampleSummary]:
        """Each slug's own newest sampled snapshot with its instant, in one batched read
        (`bzh:bulk-reconstitution`) — the snapshot :meth:`latest_external_usage_windows_by_slug`
        decodes, a failed attempt never masking it. A slug never sampled is absent."""
        ...

    def external_usage_sample_at(self, slug: str, at: datetime) -> ExternalUsageSampleSummary | None:
        """This ``slug``'s newest sampled snapshot taken at or before ``at``, a same-instant tie going to the later
        row — what the subscription looked like at that instant. A failed attempt never masks it; ``None`` when no
        sample of the slug is that old."""
        ...

    def latest_external_usage_attempt(self, slug: str) -> ExternalUsageAttemptSummary | None:
        """This ``slug``'s own newest attempt row, or ``None`` when never attempted."""
        ...

    def latest_external_usage_attempts_by_slug(self, slugs: Sequence[str]) -> dict[str, ExternalUsageAttemptSummary]:
        """:meth:`latest_external_usage_attempt` for every slug in ``slugs``, in one batched read
        (`bzh:bulk-reconstitution`). A slug never attempted is absent, which the caller reads as ``None``."""
        ...


class IWriteExternalUsageAttemptRepository(Protocol):
    """The one external-usage write a runner holds: the attempt an unregistered host records through its first
    registered runner, whose outbound report is a row of that runner's."""

    def record_external_usage_attempt(
        self,
        *,
        slug: str,
        sampled_at: datetime,
        payload: str | None,
        report_kind: str,
        report_payload: str,
        miss_reason: str | None = None,
    ) -> int | None:
        """Append one declared subscription's sampling attempt and, when it carries a report,
        buffer that report — atomically, returning the buffered seq or ``None``.
        ``slug`` is the cadence's join key. ``miss_reason`` is a
        ``SampleMissReason`` value on a miss, ``None`` on success."""
        ...


class IWriteExternalUsageRepository(
    IReadExternalUsageRepository, IWriteCredentialRenewalRepository, IWriteExternalUsageAttemptRepository, Protocol
):
    """Read-write external-usage samples and credential renewals — held only by the sampling step, the renewal pass
    and retention."""

    def prune_external_usage_samples(self, *, now: datetime) -> int:
        """Compact external-usage-sample attempts older than the store's own retention
        window, keeping each slug's newest attempt regardless of age —
        ``max(sampled_at)`` per slug is unchanged, so
        :meth:`~IReadExternalUsageRepository.last_external_usage_attempt_at` answers identically
        before and after. Returns the number of rows pruned."""
        ...


@domain_model
@dataclass(frozen=True)
class HostBufferedFact:
    """One fact waiting on the host's own lane: its per-host ``seq``, kind, and JSON payload text."""

    seq: int
    kind: str
    payload: str


class IReadHostLaneRepository(Protocol):
    """Read the host's own lane."""

    def host_buffered_count(self) -> int:
        """How many facts wait on the host's lane, unanswered by its hub."""
        ...

    def host_high_water_seen(self) -> int | None:
        """The greatest seq the hub answered applied on the host's lane, or ``None`` before its first answer."""
        ...

    def pending_host_facts(self, *, limit: int) -> list[HostBufferedFact]:
        """The oldest ``limit`` facts waiting on the host's lane, in seq order."""
        ...


class IWriteHostLaneMarkRepository(IReadHostLaneRepository, Protocol):
    """The host's lane drain: reads what waits, and clears what its hub answered."""

    def record_host_high_water(self, *, seq: int, at: datetime) -> None:
        """Record the hub's answered mark ``seq`` and delete every buffered fact at or below it, in one
        transaction. A mark below the recorded one moves nothing back."""
        ...


class IWriteHostLaneAppendRepository(Protocol):
    """The sampling pass's append to the host's lane."""

    def record_host_external_usage_attempt(
        self,
        *,
        slug: str,
        sampled_at: datetime,
        payload: str | None,
        report_kind: str,
        report_payload: str,
        miss_reason: str | None = None,
    ) -> int:
        """Append one declared subscription's sampling attempt and buffer its report on the host's own outbound
        lane — atomically, returning the buffered seq. The arguments are
        :meth:`~IWriteExternalUsageRepository.record_external_usage_attempt`'s; the report carries the same kind
        and payload."""
        ...


class IWriteHostLaneRepository(IWriteHostLaneMarkRepository, IWriteHostLaneAppendRepository, Protocol):
    """Read-write host lane — the drain's mark-write and the sampling pass's append, composed for
    :class:`~blizzard.runner.stores.HostStores`."""
