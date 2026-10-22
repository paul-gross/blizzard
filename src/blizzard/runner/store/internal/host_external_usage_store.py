"""SQLAlchemy adapter for the external-usage-sample and credential-renewal repository seam (package-private).

Host-scoped: every table it reads and writes is a fact about this host's subscriptions, never a runner's, so its
statements carry no runner equality. Its one cross-kind write is
:meth:`ExternalUsageStore.record_external_usage_attempt`, whose outbound report is a runner-scoped row stamped by the
runner's builders in the sample's own transaction."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Row, and_, func, select

from blizzard.foundation.credential_renewal import RenewalFailureReason, RenewalResult
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.batching import id_batches
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.store.internal.base import insert_outbox_row
from blizzard.runner.store.schema import (
    credential_renewal_claims,
    credential_renewal_outcomes,
    external_usage_samples,
    outbound_buffer,
)
from blizzard.runner.store.scoped import RunnerStoreConnections
from blizzard.runner.subscriptions.credential_renewer import RenewalOutcome
from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageWindow
from blizzard.runner.usage.repository import (
    CredentialRenewalSummary,
    ExternalUsageAttemptSummary,
    ExternalUsageSampleSummary,
    IWriteExternalUsageRepository,
)

_log = get_logger("blizzard.runner.store")

# See IWriteExternalUsageRepository.prune_external_usage_samples's own docstring for the retention contract.
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


class ExternalUsageStore:
    """Read-write external-usage-sample and credential-renewal adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def last_external_usage_attempt_at(self, slug: str) -> datetime | None:
        stmt = select(func.max(external_usage_samples.c.sampled_at)).where(external_usage_samples.c.slug == slug)
        with self._store.connect() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return value

    def latest_external_usage_windows(self, slug: str) -> tuple[ExternalSubscriptionUsageWindow, ...]:
        # Only rows with a non-NULL payload.
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
        # Only rows with a non-NULL payload.
        newest = self._newest_rows_by_slug(slugs, external_usage_samples.c.payload.is_not(None))
        return {slug: windows for slug, row in newest.items() if (windows := _decode_windows(row.payload))}

    def latest_external_usage_samples_by_slug(self, slugs: Sequence[str]) -> dict[str, ExternalUsageSampleSummary]:
        newest = self._newest_rows_by_slug(slugs, external_usage_samples.c.payload.is_not(None))
        return {
            slug: ExternalUsageSampleSummary(
                slug=slug, sampled_at=as_utc(row.sampled_at), windows=_decode_windows(row.payload)
            )
            for slug, row in newest.items()
        }

    def external_usage_sample_at(self, slug: str, at: datetime) -> ExternalUsageSampleSummary | None:
        # Served by `ix_external_usage_samples_slug_sampled_at`: one range-bounded seek, newest first.
        samples = external_usage_samples
        stmt = (
            select(samples.c.sampled_at, samples.c.payload)
            .where(samples.c.slug == slug, samples.c.sampled_at <= at, samples.c.payload.is_not(None))
            .order_by(samples.c.sampled_at.desc(), samples.c.id.desc())
            .limit(1)
        )
        with self._store.connect() as conn:
            row = conn.execute(stmt).one_or_none()
        if row is None:
            return None
        return ExternalUsageSampleSummary(
            slug=slug, sampled_at=as_utc(row.sampled_at), windows=_decode_windows(row.payload)
        )

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
                    select(samples.c.slug, samples.c.sampled_at, samples.c.payload, samples.c.miss_reason)
                    .join(latest, and_(samples.c.slug == latest.c.slug, samples.c.sampled_at == latest.c.sampled_at))
                    .where(*conditions)
                    .order_by(samples.c.id.desc())
                )
                for row in conn.execute(stmt):
                    # Highest id first, so a slug's first row is its same-instant tie's winner.
                    newest.setdefault(str(row.slug), row)
        return newest

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
                seq = insert_outbox_row(
                    self._store,
                    conn,
                    outbound_buffer,
                    kind=report_kind,
                    chunk_id=None,
                    lease_id=None,
                    payload=report_payload,
                    created_at=sampled_at,
                )
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
        ``max(claimed_at)`` for portability, a same-instant tie broken on ``id`` in the statement's
        own order."""
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
                    .order_by(claims.c.id.desc())
                )
                for row in conn.execute(stmt):
                    # Highest id first, so a slug's first row is its same-instant tie's winner.
                    newest.setdefault(str(row.slug), row)
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


def _conforms_external_usage_store(x: ExternalUsageStore) -> IWriteExternalUsageRepository:
    return x
