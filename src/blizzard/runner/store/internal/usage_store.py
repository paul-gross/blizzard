"""SQLAlchemy adapter for the usage/context-sample repository seam (package-private)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Connection, Row, and_, case, func, select

from blizzard.foundation.credential_renewal import RenewalFailureReason, RenewalResult
from blizzard.foundation.fact_kinds import USAGE_RECORDED
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.batching import id_batches
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.usage import SessionCostBasis, UsageSample
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.schema import (
    context_samples,
    credential_renewal_claims,
    credential_renewal_outcomes,
    external_usage_samples,
    leases,
    outbound_buffer,
    usage_facts,
)
from blizzard.runner.subscriptions.credential_renewer import RenewalOutcome
from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageWindow
from blizzard.runner.usage.repository import (
    ContextSampleState,
    CredentialRenewalSummary,
    ExternalUsageAttemptSummary,
    InvocationCost,
    IWriteUsageRepository,
    UsageTotals,
)

_log = get_logger("blizzard.runner.store")

# See IWriteUsageRepository.prune_external_usage_samples's own docstring for the retention contract.
_EXTERNAL_USAGE_SAMPLE_RETENTION_WINDOW = timedelta(days=1)

# See IWriteCredentialRenewalRepository.prune_credential_renewals's own docstring for the retention contract.
_CREDENTIAL_RENEWAL_RETENTION_WINDOW = timedelta(days=1)


def _decode_windows(payload: str | None) -> tuple[ExternalSubscriptionUsageWindow, ...]:
    if not payload:
        return ()
    decoded = json.loads(payload)
    return tuple(
        ExternalSubscriptionUsageWindow(
            window=window["window"],
            utilization_pct=window["utilization_pct"],
            resets_at=as_utc(datetime.fromisoformat(window["resets_at"])),
            window_seconds=window["window_seconds"],
        )
        for window in decoded.get("windows", [])
    )


def _attempt_summary(slug: str, row: Row) -> ExternalUsageAttemptSummary:
    return ExternalUsageAttemptSummary(
        slug=slug,
        sampled_at=as_utc(row.sampled_at),
        ok=row.payload is not None,
        miss_reason=row.miss_reason,
    )


def _renewal_summary(row: Row) -> CredentialRenewalSummary:
    """A claim row outer-joined to its outcome: no outcome on record reads as unrecorded."""
    return CredentialRenewalSummary(
        slug=str(row.slug),
        attempted_at=as_utc(row.claimed_at),
        result=RenewalResult.recognized(row.kind) or RenewalResult.UNRECORDED,
        failure_reason=RenewalFailureReason.recognized(row.failure_reason),
    )


class UsageStore:
    """Read-write usage/context-sample adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def usage_since(self, at: datetime) -> UsageTotals:
        stmt = select(
            func.coalesce(func.sum(usage_facts.c.input_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.output_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.cache_read_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.cache_create_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.cost_usd), 0.0),
            func.coalesce(func.sum(case((usage_facts.c.cost_usd.is_(None), 1), else_=0)), 0),
        ).where(usage_facts.c.recorded_at >= at)
        with self._store.connect() as conn:
            row = conn.execute(stmt).one()
        return UsageTotals(
            input_tokens=int(row[0]),
            output_tokens=int(row[1]),
            cache_read_tokens=int(row[2]),
            cache_create_tokens=int(row[3]),
            cost_usd=float(row[4]),
            cost_partial=bool(row[5]),
        )

    def session_cost_basis(self, lease_id: str) -> SessionCostBasis | None:
        with self._store.connect() as conn:
            return self._session_cost_basis(conn, lease_id)

    def _session_cost_basis(self, conn: Connection, lease_id: str) -> SessionCostBasis | None:
        row = conn.execute(
            select(leases.c.session_id, leases.c.harness_id).where(leases.c.lease_id == lease_id)
        ).one_or_none()
        if row is None or not row.session_id or not row.harness_id:
            return None
        tokens = (
            usage_facts.c.input_tokens
            + usage_facts.c.output_tokens
            + usage_facts.c.cache_read_tokens
            + usage_facts.c.cache_create_tokens
        )
        banked = conn.execute(
            select(
                func.coalesce(func.sum(tokens), 0),
                # Shares sum. A pre-reading row's figure does not — it is a running total, so
                # the largest of them already stands for everything banked before the change.
                func.coalesce(func.sum(case((usage_facts.c.cost_is_share, usage_facts.c.cost_usd))), 0.0)
                + func.coalesce(func.max(case((~usage_facts.c.cost_is_share, usage_facts.c.reported_cost_usd))), 0.0),
            )
            .select_from(usage_facts.join(leases, leases.c.lease_id == usage_facts.c.lease_id))
            .where(and_(leases.c.session_id == row.session_id, leases.c.harness_id == row.harness_id))
        ).one()
        return SessionCostBasis(token_total=int(banked[0]), banked_cost_usd=float(banked[1]))

    def last_external_usage_attempt_at(self, slug: str) -> datetime | None:
        stmt = select(func.max(external_usage_samples.c.sampled_at)).where(external_usage_samples.c.slug == slug)
        with self._store.connect() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return value

    def latest_external_usage_windows(self, slug: str) -> tuple[ExternalSubscriptionUsageWindow, ...]:
        # A NULL-payload row is a recorded failed-sample attempt — excluded here so a
        # sampler miss never hides an older still-valid 100%-utilized window behind it,
        # which would silently drop the fallback reset time.
        stmt = (
            select(external_usage_samples.c.payload)
            .where(and_(external_usage_samples.c.slug == slug, external_usage_samples.c.payload.is_not(None)))
            .order_by(external_usage_samples.c.sampled_at.desc(), external_usage_samples.c.id.desc())
            .limit(1)
        )
        with self._store.connect() as conn:
            payload = conn.execute(stmt).scalar_one_or_none()
        return _decode_windows(payload)

    def latest_external_usage_windows_by_slug(
        self, slugs: Sequence[str]
    ) -> dict[str, tuple[ExternalSubscriptionUsageWindow, ...]]:
        # Same rule as the singular: a NULL-payload row is a failed attempt and never hides an older window.
        newest = self._newest_rows_by_slug(slugs, external_usage_samples.c.payload.is_not(None))
        return {slug: windows for slug, row in newest.items() if (windows := _decode_windows(row.payload))}

    def latest_external_usage_attempt(self, slug: str) -> ExternalUsageAttemptSummary | None:
        stmt = (
            select(
                external_usage_samples.c.sampled_at,
                external_usage_samples.c.payload,
                external_usage_samples.c.miss_reason,
            )
            .where(external_usage_samples.c.slug == slug)
            .order_by(external_usage_samples.c.sampled_at.desc(), external_usage_samples.c.id.desc())
            .limit(1)
        )
        with self._store.connect() as conn:
            row = conn.execute(stmt).one_or_none()
        if row is None:
            return None
        return _attempt_summary(slug, row)

    def latest_external_usage_attempts_by_slug(self, slugs: Sequence[str]) -> dict[str, ExternalUsageAttemptSummary]:
        newest = self._newest_rows_by_slug(slugs, None)
        return {slug: _attempt_summary(slug, row) for slug, row in newest.items()}

    def _newest_rows_by_slug(self, slugs: Sequence[str], where: object | None) -> dict[str, Row]:
        """Each slug's newest ``external_usage_samples`` row among those matching ``where`` — the
        singulars' ``(sampled_at, id)`` order, kept portable by joining each slug's ``max(sampled_at)``
        back and breaking a same-instant tie on ``id`` in the statement's own order."""
        if not slugs:
            return {}
        samples = external_usage_samples
        newest: dict[str, Row] = {}
        with self._store.connect() as conn:
            for batch in id_batches(slugs):
                conditions = [samples.c.slug.in_(batch)]
                if where is not None:
                    conditions.append(where)  # type: ignore[arg-type]
                latest = (
                    select(samples.c.slug, func.max(samples.c.sampled_at).label("sampled_at"))
                    .where(*conditions)
                    .group_by(samples.c.slug)
                    .subquery()
                )
                stmt = (
                    select(
                        samples.c.id,
                        samples.c.slug,
                        samples.c.sampled_at,
                        samples.c.payload,
                        samples.c.miss_reason,
                    )
                    .join(latest, and_(samples.c.slug == latest.c.slug, samples.c.sampled_at == latest.c.sampled_at))
                    .where(*conditions)
                    .order_by(samples.c.id.desc())
                )
                for row in conn.execute(stmt):
                    # Highest id first, so a slug's first row is its same-instant tie's winner.
                    newest.setdefault(str(row.slug), row)
        return newest

    def context_sample_state(self, lease_id: str) -> ContextSampleState | None:
        stmt = select(
            func.max(context_samples.c.sampled_at).label("last_sampled_at"),
            func.max(context_samples.c.context_tokens).label("max_context_tokens"),
        ).where(context_samples.c.lease_id == lease_id)
        rows = self._store.all(stmt)
        # An aggregate over no rows is one row of NULLs, not zero rows — the NULL is the
        # "never sampled" signal here, never a `0` that would read as a real measurement.
        if not rows or rows[0].last_sampled_at is None:
            return None
        row = rows[0]
        return ContextSampleState(
            last_sampled_at=as_utc(row.last_sampled_at),  # the anchor is subtracted from `now`
            # NULL here means every attempt so far measured nothing — `MAX` skips NULLs, so this
            # is only NULL when no row carries a measurement at all.
            max_context_tokens=int(row.max_context_tokens) if row.max_context_tokens is not None else None,
        )

    def context_sample_states(self, lease_ids: Sequence[str]) -> dict[str, ContextSampleState]:
        if not lease_ids:
            return {}
        result: dict[str, ContextSampleState] = {}
        with self._store.connect() as conn:
            for batch in id_batches(lease_ids):
                stmt = (
                    select(
                        context_samples.c.lease_id,
                        func.max(context_samples.c.sampled_at).label("last_sampled_at"),
                        func.max(context_samples.c.context_tokens).label("max_context_tokens"),
                    )
                    .where(context_samples.c.lease_id.in_(batch))
                    .group_by(context_samples.c.lease_id)
                )
                for row in conn.execute(stmt):
                    result[str(row.lease_id)] = ContextSampleState(
                        last_sampled_at=as_utc(row.last_sampled_at),
                        max_context_tokens=int(row.max_context_tokens) if row.max_context_tokens is not None else None,
                    )
        return result

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
        # Both writes, one transaction: a usage fact the hub is never told about is never
        # reconciled later.
        with self._store.begin() as conn:
            existing = conn.execute(
                select(usage_facts.c.id).where(
                    and_(
                        usage_facts.c.lease_id == lease_id,
                        usage_facts.c.generation == generation,
                        usage_facts.c.kind == sample.kind,
                    )
                )
            ).one_or_none()
            if existing is not None:
                # A replay of the exact same invocation — the row is already durable;
                # write nothing a second time.
                return None
            conn.execute(
                usage_facts.insert().values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    generation=generation,
                    kind=sample.kind,
                    model=sample.model,
                    harness_id=sample.harness_id,
                    harness_version=sample.harness_version,
                    input_tokens=sample.input_tokens,
                    output_tokens=sample.output_tokens,
                    cache_read_tokens=sample.cache_read_tokens,
                    cache_create_tokens=sample.cache_create_tokens,
                    cost_usd=cost.cost_usd,
                    reported_cost_usd=sample.cost_usd,
                    estimated_cost_usd=cost.estimated_cost_usd,
                    cost_is_share=True,
                    recorded_at=recorded_at,
                )
            )
            payload = json.dumps(
                {
                    "chunk_id": chunk_id,
                    "node_id": node_id,
                    "epoch": epoch,
                    "kind": sample.kind,
                    "model": sample.model,
                    "harness_id": sample.harness_id,
                    "harness_version": sample.harness_version,
                    "input_tokens": sample.input_tokens,
                    "output_tokens": sample.output_tokens,
                    "cache_read_tokens": sample.cache_read_tokens,
                    "cache_create_tokens": sample.cache_create_tokens,
                    "cost_usd": cost.cost_usd,
                    # The same value the `usage_facts` row above keeps.
                    "estimated_cost_usd": cost.estimated_cost_usd,
                }
            )
            result = conn.execute(
                outbound_buffer.insert().values(
                    kind=USAGE_RECORDED,
                    chunk_id=chunk_id,
                    lease_id=lease_id,
                    payload=payload,
                    created_at=recorded_at,
                )
            )
        _log.info(
            "usage fact recorded",
            lease_id=lease_id,
            chunk_id=chunk_id,
            generation=generation,
            kind=sample.kind,
            cost_usd=cost.cost_usd,
        )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

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
        # The sample row and any outbound report land in ONE transaction, as the external
        # usage sampler below does — a warning buffered without its sample would re-fire.
        seq: int | None = None
        with self._store.begin() as conn:
            conn.execute(
                context_samples.insert().values(
                    lease_id=lease_id,
                    session_id=session.session_id,
                    harness_id=session.harness_id,
                    context_tokens=context_tokens,
                    sampled_at=sampled_at,
                )
            )
            if report_kind:
                result = conn.execute(
                    outbound_buffer.insert().values(
                        kind=report_kind,
                        chunk_id=chunk_id,
                        lease_id=lease_id,
                        payload=report_payload,
                        created_at=sampled_at,
                    )
                )
                key = result.inserted_primary_key
                seq = int(key[0]) if key is not None else 0
        if report_kind:
            _log.warning(
                "session context crossed the warn line",
                lease_id=lease_id,
                session_id=session.session_id,
                harness_id=session.harness_id,
                context_tokens=context_tokens,
            )
        return seq

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
        # The attempt row and its outbound report land in ONE transaction. Runner-scoped
        # (`chunk_id=None, lease_id=None`): a fact about the account, not a chunk or lease.
        seq: int | None = None
        with self._store.begin() as conn:
            conn.execute(
                external_usage_samples.insert().values(
                    slug=slug, sampled_at=sampled_at, payload=payload, miss_reason=miss_reason
                )
            )
            # A report buffers whenever the attempt carries one (`report_kind` non-empty), not
            # only with a sample `payload`: a miss buffers its `missed` report with a NULL payload.
            if report_kind:
                result = conn.execute(
                    outbound_buffer.insert().values(
                        kind=report_kind, chunk_id=None, lease_id=None, payload=report_payload, created_at=sampled_at
                    )
                )
                key = result.inserted_primary_key
                seq = int(key[0]) if key is not None else 0
        _log.info("external subscription usage attempt recorded", slug=slug, sampled=payload is not None)
        return seq

    def prune_external_usage_samples(self, *, now: datetime) -> int:
        cutoff = now - _EXTERNAL_USAGE_SAMPLE_RETENTION_WINDOW
        newest_sample = external_usage_samples.alias("newest_sample")
        latest_sample = (
            select(func.max(newest_sample.c.sampled_at))
            .where(newest_sample.c.slug == external_usage_samples.c.slug)
            .scalar_subquery()
        )
        with self._store.begin() as conn:
            # `< latest_sample` (never `<=`) keeps EVERY row tied for newest — a same-instant
            # pair is not a superseded attempt, so neither is pruned out from under the other.
            result = conn.execute(
                external_usage_samples.delete().where(
                    and_(
                        external_usage_samples.c.sampled_at < cutoff,
                        external_usage_samples.c.sampled_at < latest_sample,
                    )
                )
            )
        return result.rowcount

    def last_credential_renewal_claim_at(self, slug: str) -> datetime | None:
        stmt = select(func.max(credential_renewal_claims.c.claimed_at)).where(credential_renewal_claims.c.slug == slug)
        with self._store.connect() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return as_utc(value) if value is not None else None

    def latest_credential_renewals_by_slug(self, slugs: Sequence[str]) -> dict[str, CredentialRenewalSummary]:
        """Each slug's newest claim by ``(claimed_at, id)`` — joined back to its slug's
        ``max(claimed_at)`` for portability, a same-instant tie broken on ``id`` here."""
        if not slugs:
            return {}
        claims, outcomes = credential_renewal_claims, credential_renewal_outcomes
        newest: dict[str, Row] = {}
        with self._store.connect() as conn:
            for batch in id_batches(slugs):
                latest = (
                    select(claims.c.slug, func.max(claims.c.claimed_at).label("claimed_at"))
                    .where(claims.c.slug.in_(batch))
                    .group_by(claims.c.slug)
                    .subquery()
                )
                stmt = (
                    select(claims.c.id, claims.c.slug, claims.c.claimed_at, outcomes.c.kind, outcomes.c.failure_reason)
                    .join(latest, and_(claims.c.slug == latest.c.slug, claims.c.claimed_at == latest.c.claimed_at))
                    .outerjoin(outcomes, outcomes.c.claim_id == claims.c.id)
                    .where(claims.c.slug.in_(batch))
                )
                for row in conn.execute(stmt):
                    held = newest.get(str(row.slug))
                    if held is None or row.id > held.id:
                        newest[str(row.slug)] = row
        return {slug: _renewal_summary(row) for slug, row in newest.items()}

    def claim_credential_renewal(self, *, slug: str, claimed_at: datetime) -> int:
        with self._store.begin() as conn:
            result = conn.execute(credential_renewal_claims.insert().values(slug=slug, claimed_at=claimed_at))
        key = result.inserted_primary_key
        assert key is not None  # an autoincrement insert always returns its key
        _log.info("credential renewal claimed", slug=slug)
        return int(key[0])

    def record_credential_renewal_outcome(
        self, *, claim_id: int, outcome: RenewalOutcome, recorded_at: datetime
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                credential_renewal_outcomes.insert().values(
                    claim_id=claim_id,
                    kind=outcome.kind.value,
                    failure_reason=outcome.failure_reason.value if outcome.failure_reason is not None else None,
                    recorded_at=recorded_at,
                )
            )
        _log.info("credential renewal outcome recorded", claim_id=claim_id, kind=outcome.kind.value)

    def prune_credential_renewals(self, *, now: datetime) -> int:
        cutoff = now - _CREDENTIAL_RENEWAL_RETENTION_WINDOW
        claims = credential_renewal_claims
        newest_claim = claims.alias("newest_claim")
        latest_claim = (
            select(func.max(newest_claim.c.claimed_at)).where(newest_claim.c.slug == claims.c.slug).scalar_subquery()
        )
        # `< latest_claim` (never `<=`) keeps every claim tied for newest, as the attempt prune does.
        superseded = and_(claims.c.claimed_at < cutoff, claims.c.claimed_at < latest_claim)
        with self._store.begin() as conn:
            # Outcomes first, while their claims still identify them.
            conn.execute(
                credential_renewal_outcomes.delete().where(
                    credential_renewal_outcomes.c.claim_id.in_(select(claims.c.id).where(superseded))
                )
            )
            result = conn.execute(claims.delete().where(superseded))
        return result.rowcount


def _conforms_usage_store(x: UsageStore) -> IWriteUsageRepository:
    return x
