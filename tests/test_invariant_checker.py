"""Component coverage for the facts-level invariant checker (``bzh:invariant-checker``).

Migrates real hub + runner stores, asserts a clean store yields no violations, then
injects each kind of corruption and asserts the matching invariant is named. This is the
library the kill-9 sweep asserts after every armed crash — here it is exercised without
subprocesses so the default gate covers it.

One check is a partial exception to "inject the corruption on a head-migrated store":
``hub:pr-opened-idempotent`` guards a violation ``uq_delivery_pr_opened_chunk_repo``
(0014_hub_pr_opened_idempotent) now makes impossible to *write* on a head-migrated
store — a raw two-insert seed the way every sibling check above does it dies on the
constraint instead of producing a violation. That test seeds a store migrated only to
0013 (the schema this checker's query still reads), the same shape
``test_pr_opened_migration.py`` seeds to exercise the migration itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.invariants import check_hub_store, check_runner_store
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import init_environment as init_hub
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as hub
from blizzard.runner.runtime import init_environment as init_runner
from blizzard.runner.store import schema as runner

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 14, tzinfo=UTC)
_BEFORE_PR_OPENED_CONSTRAINT = "0013_hub_pm_pointer_source_ref"  # the head just before the unique constraint

# The pre-0014 shape: no unique constraint, so two rows for the same (chunk_id, repo)
# are legal to seed — mirrors ``test_pr_opened_migration.py``'s ``_OLD_PR_OPENED``.
_OLD_PR_OPENED = sa.Table(
    "delivery_pr_opened",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("pr_number", sa.Integer, nullable=False),
    sa.Column("pr_url", sa.String, nullable=False),
    sa.Column("commit_hash", sa.String, nullable=False),
    sa.Column("opened_at", sa.DateTime, nullable=False),
)


def _runner_engine(tmp_path: Path):
    return create_engine_from_url(init_runner(tmp_path / "runner").db_url)


def _hub_engine(tmp_path: Path):
    return create_engine_from_url(init_hub(tmp_path / "hub").db_url)


def test_clean_stores_have_no_violations(tmp_path: Path) -> None:
    assert check_runner_store(_runner_engine(tmp_path)) == []
    assert check_hub_store(_hub_engine(tmp_path)) == []


def test_two_live_leases_for_one_chunk_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for lease_id in ("lease_a", "lease_b"):
            conn.execute(
                insert(runner.leases).values(
                    lease_id=lease_id, chunk_id="ch_1", epoch=1, runner_id="r", created_at=_NOW
                )
            )
    slugs = {v.invariant for v in check_runner_store(engine)}
    assert "runner:one-live-lease-per-chunk" in slugs
    # Closing one lease clears the violation (facts-not-status: a closure is the fact).
    with engine.begin() as conn:
        conn.execute(
            insert(runner.lease_closures).values(
                lease_id="lease_b", chunk_id="ch_1", node_id="nd", reason="transitioned", closed_at=_NOW
            )
        )
    assert check_runner_store(engine) == []


def test_env_bound_to_two_chunks_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for chunk_id in ("ch_1", "ch_2"):
            conn.execute(
                insert(runner.env_bindings).values(
                    chunk_id=chunk_id, environment_id="env_shared", workdir="/w", bound_at=_NOW
                )
            )
    slugs = {v.invariant for v in check_runner_store(engine)}
    assert "runner:unique-env-binding" in slugs


def test_gapped_outbound_seq_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for seq in (1, 2, 4):  # 3 is missing — a hole in the FIFO buffer
            conn.execute(
                insert(runner.outbound_buffer).values(
                    seq=seq, kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=_NOW
                )
            )
    slugs = {v.invariant for v in check_runner_store(engine)}
    assert "runner:gapless-outbound-seq" in slugs


def test_duplicate_repo_land_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        for _ in range(2):
            conn.execute(
                insert(hub.delivery_repo_landed).values(
                    chunk_id="ch_1", repo="toy-api", commit_hash="abc", landed_at=_NOW
                )
            )
    slugs = {v.invariant for v in check_hub_store(engine)}
    assert "hub:per-repo-land-idempotent" in slugs


def test_duplicate_pr_opened_is_a_violation(tmp_path: Path) -> None:
    """Seeded pre-0014 (see the module docstring): ``uq_delivery_pr_opened_chunk_repo``
    only exists from 0014 on, so a store migrated to head can no longer produce this
    violation by a raw duplicate insert — it would raise ``IntegrityError`` instead."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner_ = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner_.upgrade(_BEFORE_PR_OPENED_CONSTRAINT)
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        for pk in (1, 2):
            conn.execute(
                sa.insert(_OLD_PR_OPENED).values(
                    id=pk,
                    chunk_id="ch_1",
                    repo="acme/widget",
                    pr_number=1,
                    pr_url="http://forge/acme/widget/pull/1",
                    commit_hash="abc123",
                    opened_at=_NOW,
                )
            )
    slugs = {v.invariant for v in check_hub_store(engine)}
    assert "hub:pr-opened-idempotent" in slugs


def test_transition_epoch_beyond_latest_lease_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(hub.lease_facts).values(chunk_id="ch_1", epoch=1, runner_id="r", minted_at=_NOW))
        conn.execute(
            insert(hub.transitions).values(
                transition_id="tr_1",
                chunk_id="ch_1",
                from_node_id="nd_a",
                to_node_id="nd_b",
                choice_name="pass",
                epoch=2,  # a transition fenced beyond any known lease — a zombie land
                runner_id="r",
                recorded_at=_NOW,
            )
        )
    slugs = {v.invariant for v in check_hub_store(engine)}
    assert "hub:epoch-consistent-transitions" in slugs
