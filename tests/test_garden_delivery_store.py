"""``GardenDeliveryStore`` — the garden-delivery-materialization repository (blizzard#393
Phase 3, component tier). Migrated-to-head sqlite-on-disk — the
``tests/test_garden_proposal_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.garden_delivery_materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    FindingFactRecord,
    NewFinding,
    NewFindingSet,
    NewProposal,
)
from blizzard.hub.domain.run_context import RunContext
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.schema import (
    artifacts,
    finding_facts,
    finding_sets,
    findings,
    garden_proposal_findings,
    garden_proposals,
)
from tests.support import hub_store_connections, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_RUN = RunContext(routine_name="nightly", scope_slug="blizzard", mode="dry_run")


def _store_and_engine(tmp_path: Path) -> tuple[GardenDeliveryStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO scopes (slug, description, created_at) VALUES ('blizzard', '', :now)"),
            {"now": _NOW},
        )
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
    store_connections = hub_store_connections(engine)
    return GardenDeliveryStore(store_connections), engine


def _full_plan(*, at: datetime = _NOW) -> DeliveryPlan:
    return DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="garden-survey",
        epoch=1,
        at=at,
        run=_RUN,
        new_findings=[
            NewFinding(
                finding_id="fin_1",
                routine_name="nightly",
                scope_slug="blizzard",
                class_="stale-docstring",
                locus="a.py:1",
                summary="s1",
                introduced=None,
            )
        ],
        facts=[
            FindingFactRecord(finding_id="fin_1", kind="add", note=None),
            FindingFactRecord(finding_id="fin_2", kind="observed", note=None),
            FindingFactRecord(finding_id="fin_3", kind="gone", note="couldn't reproduce"),
        ],
        finding_sets=[
            NewFindingSet(
                finding_set_id="fins_1",
                artifact_id="art_placeholder",
                scope_slug="blizzard",
                revisions={"blizzard": "abc1234"},
                measurement="12.3s",
            )
        ],
        proposals=[
            NewProposal(
                proposal_id="gprop_1",
                routine_name="nightly",
                class_="fix-the-source",
                title="Author a docstring standard",
                body="the case",
                finding_ids=["fin_1"],
            )
        ],
    )


def test_deliver_writes_every_row(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    plan = _full_plan()

    outcome = store.deliver(plan)

    assert outcome is DeliveryOutcome.RECORDED
    with engine.connect() as conn:
        finding_rows = conn.execute(sa.select(findings)).all()
        assert [r.finding_id for r in finding_rows] == ["fin_1"]
        assert finding_rows[0].class_ == "stale-docstring"

        fact_rows = conn.execute(sa.select(finding_facts).order_by(finding_facts.c.id)).all()
        assert [(r.finding_id, r.kind, r.note) for r in fact_rows] == [
            ("fin_1", "add", None),
            ("fin_2", "observed", None),
            ("fin_3", "gone", "couldn't reproduce"),
        ]

        set_rows = conn.execute(sa.select(finding_sets)).all()
        assert len(set_rows) == 1
        assert set_rows[0].finding_set_id == "fins_1"
        assert set_rows[0].chunk_id == "ch_1"
        assert set_rows[0].artifact_id == "art_placeholder"

        proposal_rows = conn.execute(sa.select(garden_proposals)).all()
        assert [r.proposal_id for r in proposal_rows] == ["gprop_1"]

        link_rows = conn.execute(sa.select(garden_proposal_findings)).all()
        assert [(r.proposal_id, r.finding_id) for r in link_rows] == [("gprop_1", "fin_1")]

        marker_rows = conn.execute(sa.select(artifacts).where(artifacts.c.name == "garden-delivered")).all()
        assert len(marker_rows) == 1
        assert marker_rows[0].chunk_id == "ch_1"
        assert marker_rows[0].node_id == "nd_1"
        assert marker_rows[0].epoch == 1


def test_deliver_replay_mints_nothing_new(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    plan = _full_plan()
    first = store.deliver(plan)
    assert first is DeliveryOutcome.RECORDED

    second = store.deliver(plan)

    assert second is DeliveryOutcome.ALREADY_RECORDED
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(finding_facts)).scalar_one() == 3
        assert conn.execute(sa.select(sa.func.count()).select_from(finding_sets)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(garden_proposals)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(garden_proposal_findings)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(artifacts)).scalar_one() == 1


def test_deliver_at_a_new_epoch_resolving_the_same_artifact_is_already_recorded(tmp_path: Path) -> None:
    """A second delivery visit at a fresh (node_id, epoch) — e.g. after an `invalid`
    bounce to `reconcile` and back — that resolves the *same* already-materialized
    artifact (`reconcile` minted nothing new) must return ALREADY_RECORDED cleanly,
    not trip `finding_sets.artifact_id`'s unique constraint with a raw IntegrityError."""
    store, engine = _store_and_engine(tmp_path)
    first = _full_plan()
    assert store.deliver(first) is DeliveryOutcome.RECORDED

    second = DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_2",
        node_name="garden-survey",
        epoch=2,
        at=_NOW,
        run=_RUN,
        new_findings=[],
        facts=[],
        finding_sets=[
            NewFindingSet(
                finding_set_id="fins_replay",
                artifact_id="art_placeholder",  # same artifact_id as `first`'s finding set
                scope_slug="blizzard",
                revisions={"blizzard": "abc1234"},
                measurement="12.3s",
            )
        ],
        proposals=[],
    )

    outcome = store.deliver(second)

    assert outcome is DeliveryOutcome.ALREADY_RECORDED
    with engine.connect() as conn:
        set_rows = conn.execute(sa.select(finding_sets)).all()
        assert [r.finding_set_id for r in set_rows] == ["fins_1"]
        marker_rows = conn.execute(sa.select(artifacts).where(artifacts.c.name == "garden-delivered")).all()
        assert len(marker_rows) == 1


def test_deliver_clean_plan_records_only_the_finding_set(tmp_path: Path) -> None:
    """No findings, no proposals — but an artifact's scope/revisions/measurement are
    still recorded, even for an empty delta (acceptance criterion)."""
    store, engine = _store_and_engine(tmp_path)
    plan = DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_2",
        node_name="garden-survey",
        epoch=1,
        at=_NOW,
        run=_RUN,
        new_findings=[],
        facts=[],
        finding_sets=[
            NewFindingSet(
                finding_set_id="fins_2",
                artifact_id="art_clean",
                scope_slug="blizzard",
                revisions={},
                measurement=None,
            )
        ],
        proposals=[],
    )

    outcome = store.deliver(plan)

    assert outcome is DeliveryOutcome.RECORDED
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar_one() == 0
        assert conn.execute(sa.select(sa.func.count()).select_from(finding_facts)).scalar_one() == 0
        assert conn.execute(sa.select(sa.func.count()).select_from(garden_proposals)).scalar_one() == 0
        set_rows = conn.execute(sa.select(finding_sets)).all()
        assert len(set_rows) == 1
        assert set_rows[0].finding_set_id == "fins_2"
        assert set_rows[0].measurement is None
        marker_rows = conn.execute(sa.select(artifacts).where(artifacts.c.name == "garden-delivered")).all()
        assert len(marker_rows) == 1
