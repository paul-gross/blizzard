"""Move the renewal outcome off ``external_usage_samples.renewal`` into claim and outcome facts;
``downgrade()`` re-encodes each recorded outcome onto the slug's first attempt at or after its claim.

Revision ID: 20261005_1000_runner_credential_renewal_facts
Revises: 20261004_1200_runner_takeover_hold_epoch
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261005_1000_runner_credential_renewal_facts"
down_revision: str | None = "20261004_1200_runner_takeover_hold_epoch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SAMPLES = "external_usage_samples"
_RENEWAL = "renewal"
_CLAIMS = "credential_renewal_claims"
_OUTCOMES = "credential_renewal_outcomes"
_CLAIMS_INDEX = "ix_credential_renewal_claims_slug_claimed_at"

_RENEWED = "renewed"
_FAILED = "failed"
_FAILURE_REASONS = frozenset({"renewer_unavailable", "timed_out", "vendor_refused", "protocol_error"})

_metadata = sa.MetaData()

# Frozen literals (`bzh:frozen-revisions`): the two tables as this revision creates them.
_claims = sa.Table(
    _CLAIMS,
    _metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("slug", sa.String(), nullable=False),
    sa.Column("claimed_at", UtcDateTime(), nullable=False),
)

_outcomes = sa.Table(
    _OUTCOMES,
    _metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("claim_id", sa.Integer(), nullable=False, unique=True),
    sa.Column("kind", sa.String(), nullable=False),
    sa.Column("failure_reason", sa.String(), nullable=True),
    sa.Column("recorded_at", UtcDateTime(), nullable=False),
    sa.CheckConstraint("kind IN ('renewed', 'failed')", name="ck_credential_renewal_outcomes_kind"),
    sa.CheckConstraint(
        "failure_reason IS NULL OR failure_reason IN "
        "('renewer_unavailable', 'timed_out', 'vendor_refused', 'protocol_error')",
        name="ck_credential_renewal_outcomes_failure_reason",
    ),
)

# A narrow stub of the attempt table: only the columns this revision reads or writes.
_samples = sa.Table(
    _SAMPLES,
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("slug", sa.String(), nullable=False),
    sa.Column("sampled_at", UtcDateTime(), nullable=False),
    sa.Column(_RENEWAL, sa.String(), nullable=True),
)


def _parse_legacy(value: str) -> tuple[str, str | None] | None:
    """``(kind, failure_reason)`` for one legacy ``renewal`` string, or ``None`` for a value
    that names no outcome."""
    if value == _RENEWED:
        return _RENEWED, None
    if value == _FAILED:
        return _FAILED, None
    prefix = f"{_FAILED}:"
    if value.startswith(prefix):
        reason = value[len(prefix) :]
        return _FAILED, reason if reason in _FAILURE_REASONS else None
    return None


def _encode_legacy(kind: str, failure_reason: str | None) -> str:
    if kind == _FAILED and failure_reason is not None:
        return f"{_FAILED}:{failure_reason}"
    return kind


def _tables(bind: sa.Connection) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _has_renewal_column(bind: sa.Connection) -> bool:
    return _RENEWAL in {c["name"] for c in sa.inspect(bind).get_columns(_SAMPLES)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if _CLAIMS not in tables:
        _claims.create(bind)
        op.create_index(_CLAIMS_INDEX, _CLAIMS, ["slug", "claimed_at"])
    if _OUTCOMES not in tables:
        _outcomes.create(bind)
    if not _has_renewal_column(bind):
        return
    rows = bind.execute(
        sa.select(_samples.c.slug, _samples.c.sampled_at, _samples.c.renewal)
        .where(_samples.c.renewal.is_not(None))
        .order_by(_samples.c.sampled_at, _samples.c.id)
    ).all()
    for row in rows:
        parsed = _parse_legacy(row.renewal)
        if parsed is None:
            continue
        kind, failure_reason = parsed
        claim = bind.execute(_claims.insert().values(slug=row.slug, claimed_at=row.sampled_at))
        key = claim.inserted_primary_key
        assert key is not None
        bind.execute(
            _outcomes.insert().values(
                claim_id=int(key[0]), kind=kind, failure_reason=failure_reason, recorded_at=row.sampled_at
            )
        )
    with op.batch_alter_table(_SAMPLES) as batch:
        batch.drop_column(_RENEWAL)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_renewal_column(bind):
        op.add_column(_SAMPLES, sa.Column(_RENEWAL, sa.String(), nullable=True))
    tables = _tables(bind)
    if _CLAIMS in tables and _OUTCOMES in tables:
        recorded = bind.execute(
            sa.select(_claims.c.slug, _claims.c.claimed_at, _outcomes.c.kind, _outcomes.c.failure_reason)
            .select_from(_claims.join(_outcomes, _outcomes.c.claim_id == _claims.c.id))
            .order_by(_claims.c.claimed_at, _claims.c.id)
        ).all()
        for row in recorded:
            target = bind.execute(
                sa.select(_samples.c.id)
                .where(
                    _samples.c.slug == row.slug,
                    _samples.c.sampled_at >= row.claimed_at,
                    _samples.c.renewal.is_(None),
                )
                .order_by(_samples.c.sampled_at, _samples.c.id)
                .limit(1)
            ).scalar_one_or_none()
            if target is None:
                continue
            bind.execute(
                _samples.update()
                .where(_samples.c.id == target)
                .values(renewal=_encode_legacy(row.kind, row.failure_reason))
            )
    if _OUTCOMES in tables:
        op.drop_table(_OUTCOMES)
    if _CLAIMS in tables:
        op.drop_index(_CLAIMS_INDEX, table_name=_CLAIMS)
        op.drop_table(_CLAIMS)
