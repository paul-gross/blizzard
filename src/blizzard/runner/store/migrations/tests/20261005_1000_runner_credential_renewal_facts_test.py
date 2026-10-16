"""Tests proving the ``20261005_1000_runner_credential_renewal_facts`` revision."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.store import schema as runner_schema
from tests.migration_support_store_migrations import (
    assert_frozen_table_lacks_a_later_revisions_columns,
)

pytestmark = [pytest.mark.unit]

_RENEWAL_FACTS_PARENT = "20261004_1200_runner_takeover_hold_epoch"


def test_external_usage_samples_miss_reason_and_renewal_columns_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``external_usage_samples.miss_reason``/``renewal`` — both nullable,
    no backfill since no historical row carries either fact; downgrading past this
    revision drops both columns again. ``renewal`` is retired again at head."""
    config = runner_runtime.init_environment(tmp_path)  # upgrades to head
    runner = runner_runtime.migration_runner(config)

    def _columns() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns("external_usage_samples")}
        finally:
            engine.dispose()

    assert "miss_reason" in _columns()
    assert "renewal" not in _columns()

    runner.downgrade("20260922_1200_external_usage_miss_reason")
    assert {"miss_reason", "renewal"} <= _columns()

    runner.downgrade("20260921_1600_overload_facts")
    assert not ({"miss_reason", "renewal"} & _columns())

    runner.upgrade("head")
    assert "miss_reason" in _columns()
    assert "renewal" not in _columns()


def test_credential_renewal_history_moves_into_typed_facts_and_downgrades_back(tmp_path: Path) -> None:
    """Every legacy ``renewal`` string becomes a claim at its attempt's ``sampled_at`` and a typed
    outcome, and the column is retired; downgrading re-encodes each recorded outcome onto its
    attempt row, and an unrecorded claim — which had no legacy encoding — onto none."""
    config = runner_runtime.init_environment(tmp_path)
    runner = runner_runtime.migration_runner(config)
    runner.downgrade(_RENEWAL_FACTS_PARENT)
    first, second, third = (datetime(2026, 9, 22, hour, tzinfo=UTC) for hour in (10, 11, 12))
    legacy = [
        ("codex", first, "renewed"),
        ("codex", second, "failed:timed_out"),
        ("codex", third, None),
        ("other", first, "failed"),
    ]
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            # Written through the column types, exactly as the attempt writer stored them.
            legacy_samples = sa.Table(
                "external_usage_samples",
                sa.MetaData(),
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("slug", sa.String),
                sa.Column("sampled_at", UtcDateTime),
                sa.Column("renewal", sa.String),
            )
            for slug, sampled_at, renewal in legacy:
                conn.execute(legacy_samples.insert().values(slug=slug, sampled_at=sampled_at, renewal=renewal))
    finally:
        engine.dispose()

    runner.upgrade("head")

    engine = create_engine_from_url(config.db_url)
    try:
        assert "renewal" not in {c["name"] for c in sa.inspect(engine).get_columns("external_usage_samples")}
        with engine.connect() as conn:
            facts = conn.execute(
                sa.select(
                    runner_schema.credential_renewal_claims.c.slug,
                    runner_schema.credential_renewal_claims.c.claimed_at,
                    runner_schema.credential_renewal_outcomes.c.kind,
                    runner_schema.credential_renewal_outcomes.c.failure_reason,
                )
                .select_from(
                    runner_schema.credential_renewal_claims.join(
                        runner_schema.credential_renewal_outcomes,
                        runner_schema.credential_renewal_outcomes.c.claim_id
                        == runner_schema.credential_renewal_claims.c.id,
                    )
                )
                .order_by(runner_schema.credential_renewal_claims.c.id)
            ).all()
        assert [tuple(row) for row in facts] == [
            ("codex", first, "renewed", None),
            ("other", first, "failed", None),
            ("codex", second, "failed", "timed_out"),
        ]
        # A claim the pass lands later, with no outcome on record, has no legacy encoding.
        with engine.begin() as conn:
            conn.execute(runner_schema.credential_renewal_claims.insert().values(slug="codex", claimed_at=third))
    finally:
        engine.dispose()

    runner.downgrade(_RENEWAL_FACTS_PARENT)

    engine = create_engine_from_url(config.db_url)
    try:
        assert not {"credential_renewal_claims", "credential_renewal_outcomes"} & set(
            sa.inspect(engine).get_table_names()
        )
        with engine.connect() as conn:
            restored = conn.execute(
                sa.text("SELECT slug, renewal FROM external_usage_samples ORDER BY slug, sampled_at")
            ).all()
        assert [tuple(row) for row in restored] == [
            ("codex", "renewed"),
            ("codex", "failed:timed_out"),
            ("codex", None),
            ("other", "failed"),
        ]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "runner",
            "20261004_1200_runner_takeover_hold_epoch",
            "external_usage_samples",
            ("renewal",),
            "removed",
            id="runner.external_usage_samples.renewal.removed",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
