"""``ReviewFindingsStore`` — the review-findings-materialization repository (blizzard#582
Phase 1, component tier). Migrated-to-head sqlite-on-disk — the
``tests/test_garden_delivery_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.review_findings_materialize import (
    NewReviewFinding,
    ReviewFindingFactRecord,
    ReviewFindingsOutcome,
    ReviewFindingsPlan,
)
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from blizzard.hub.store.schema import artifacts, finding_facts, findings, scopes
from tests.support import hub_store_connections, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store_and_engine(tmp_path: Path) -> tuple[ReviewFindingsStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(sa.insert(scopes).values(slug="blizzard", description="", created_at=_NOW))
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
    store_connections = hub_store_connections(engine)
    return ReviewFindingsStore(store_connections), engine


def _plan(*, chunk_id: str = "ch_1", node_id: str = "nd_1", epoch: int = 1, at: datetime = _NOW) -> ReviewFindingsPlan:
    return ReviewFindingsPlan(
        chunk_id=chunk_id,
        node_id=node_id,
        node_name="record-findings",
        epoch=epoch,
        at=at,
        new_findings=[
            NewReviewFinding(
                finding_id="fin_1",
                scope_slug="blizzard",
                class_="correctness",
                locus="a.py:1",
                summary="s1",
                severity="should-fix",
                raised_by_chunk_id="ch_1",
            )
        ],
        facts=[ReviewFindingFactRecord(finding_id="fin_1", ref="F1")],
    )


def test_deliver_writes_every_row(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)

    outcome = store.deliver(_plan())

    assert outcome is ReviewFindingsOutcome.RECORDED
    with engine.connect() as conn:
        finding_rows = conn.execute(sa.select(findings)).all()
        assert len(finding_rows) == 1
        row = finding_rows[0]
        assert row.finding_id == "fin_1"
        assert row.routine_name is None
        assert row.scope_slug == "blizzard"
        assert row.class_ == "correctness"
        assert row.locus == "a.py:1"
        assert row.summary == "s1"
        assert row.source == "review"
        assert row.severity == "should-fix"
        assert row.raised_by_chunk_id == "ch_1"

        fact_rows = conn.execute(sa.select(finding_facts)).all()
        assert len(fact_rows) == 1
        assert fact_rows[0].finding_id == "fin_1"
        assert fact_rows[0].kind == "add"
        assert fact_rows[0].finding_set_id is None
        assert fact_rows[0].ref == "F1"

        marker_rows = conn.execute(sa.select(artifacts).where(artifacts.c.name == "review-findings-delivered")).all()
        assert len(marker_rows) == 1
        assert marker_rows[0].chunk_id == "ch_1"
        assert marker_rows[0].node_id == "nd_1"
        assert marker_rows[0].epoch == 1


def test_deliver_mints_an_unseen_scope_in_the_same_transaction(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    plan = ReviewFindingsPlan(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="record-findings",
        epoch=1,
        at=_NOW,
        new_findings=[
            NewReviewFinding(
                finding_id="fin_1",
                scope_slug="brand-new",
                class_="correctness",
                locus="a.py:1",
                summary="s1",
                severity="should-fix",
                raised_by_chunk_id="ch_1",
            )
        ],
        facts=[ReviewFindingFactRecord(finding_id="fin_1", ref="F1")],
    )

    outcome = store.deliver(plan)

    assert outcome is ReviewFindingsOutcome.RECORDED
    with engine.connect() as conn:
        scope_row = conn.execute(sa.select(scopes).where(scopes.c.slug == "brand-new")).one()
        assert "ch_1" in scope_row.description


def test_deliver_does_not_re_mint_an_existing_scope(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)

    store.deliver(_plan())

    with engine.connect() as conn:
        scope_row = conn.execute(sa.select(scopes).where(scopes.c.slug == "blizzard")).one()
        assert scope_row.description == ""  # the pre-seeded description survives untouched


def test_deliver_on_an_empty_plan_still_writes_a_marker_and_nothing_else(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    plan = ReviewFindingsPlan(chunk_id="ch_1", node_id="nd_1", node_name="record-findings", epoch=1, at=_NOW)

    outcome = store.deliver(plan)

    assert outcome is ReviewFindingsOutcome.RECORDED
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar_one() == 0
        assert conn.execute(sa.select(sa.func.count()).select_from(finding_facts)).scalar_one() == 0
        marker_rows = conn.execute(sa.select(artifacts).where(artifacts.c.name == "review-findings-delivered")).all()
        assert len(marker_rows) == 1


def test_deliver_replay_mints_nothing_new(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    plan = _plan()
    first = store.deliver(plan)
    assert first is ReviewFindingsOutcome.RECORDED

    second = store.deliver(plan)

    assert second is ReviewFindingsOutcome.ALREADY_RECORDED
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(finding_facts)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(artifacts)).scalar_one() == 1


def test_deliver_from_a_fresh_node_and_epoch_is_still_already_recorded(tmp_path: Path) -> None:
    """D6: the idempotence key is `chunk_id` alone, not `(chunk_id, node_id, epoch)` —
    unlike garden delivery, a fresh node/epoch visit for a chunk that already delivered
    stays a no-op."""
    store, engine = _store_and_engine(tmp_path)
    assert store.deliver(_plan()) is ReviewFindingsOutcome.RECORDED

    second = _plan(node_id="nd_2", epoch=2)
    outcome = store.deliver(second)

    assert outcome is ReviewFindingsOutcome.ALREADY_RECORDED
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar_one() == 1
        marker_rows = conn.execute(sa.select(artifacts).where(artifacts.c.name == "review-findings-delivered")).all()
        assert len(marker_rows) == 1  # no fresh marker written on an already-delivered chunk
