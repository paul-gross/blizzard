"""``GardenDeliveryStore`` — the garden-delivery-materialization repository (blizzard#393
Phase 3, component tier). Migrated-to-head sqlite-on-disk — the
``tests/test_garden_proposal_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.garden_delivery_materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    DeltaMaterialization,
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


def _store_and_engine(tmp_path: Path, *, enforce_foreign_keys: bool = False) -> tuple[GardenDeliveryStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    if enforce_foreign_keys:
        # sqlite runs with FK enforcement off by default — the very reason a wrong
        # insert order (`finding_facts` before the `finding_sets` row it references)
        # passed every other test here. Attached before the first connection so every
        # connection this engine ever opens, seeding included, enforces FKs.
        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

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
        deltas=[
            DeltaMaterialization(
                finding_set=NewFindingSet(
                    finding_set_id="fins_1",
                    artifact_id="art_placeholder",
                    scope_slug="blizzard",
                    revisions={"blizzard": "abc1234"},
                    measurement="12.3s",
                ),
                new_findings=[
                    NewFinding(
                        finding_id="fin_1",
                        routine_name="nightly",
                        scope_slug="blizzard",
                        class_="stale-docstring",
                        locus="a.py:1",
                        summary="s1",
                        introduced=None,
                        introduced_at=None,
                    )
                ],
                facts=[
                    FindingFactRecord(finding_id="fin_1", kind="add", finding_set_id="fins_1", note=None),
                    FindingFactRecord(finding_id="fin_2", kind="observed", finding_set_id="fins_1", note=None),
                    FindingFactRecord(
                        finding_id="fin_3", kind="gone", finding_set_id="fins_1", note="couldn't reproduce"
                    ),
                ],
            )
        ],
        proposals=[
            NewProposal(
                proposal_id="gprop_1",
                routine_name="nightly",
                class_="fix-the-source",
                title="Author a docstring standard",
                body="the case",
                source_artifact_id="art_docket",
                ref="p1",
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
        # Every add/observed/gone fact this delivery materialized attributes to the
        # finding_set it was delivered under (blizzard#401 D1).
        assert {r.finding_set_id for r in fact_rows} == {"fins_1"}

        set_rows = conn.execute(sa.select(finding_sets)).all()
        assert len(set_rows) == 1
        assert set_rows[0].finding_set_id == "fins_1"
        assert set_rows[0].chunk_id == "ch_1"
        assert set_rows[0].artifact_id == "art_placeholder"
        assert set_rows[0].routine_name == "nightly"

        proposal_rows = conn.execute(sa.select(garden_proposals)).all()
        assert [r.proposal_id for r in proposal_rows] == ["gprop_1"]

        link_rows = conn.execute(sa.select(garden_proposal_findings)).all()
        assert [(r.proposal_id, r.finding_id) for r in link_rows] == [("gprop_1", "fin_1")]

        marker_rows = conn.execute(sa.select(artifacts).where(artifacts.c.name == "garden-delivered")).all()
        assert len(marker_rows) == 1
        assert marker_rows[0].chunk_id == "ch_1"
        assert marker_rows[0].node_id == "nd_1"
        assert marker_rows[0].epoch == 1


def test_deliver_writes_finding_sets_before_finding_facts_under_fk_enforcement(tmp_path: Path) -> None:
    """`finding_facts.finding_set_id` references `finding_sets` — a delivery that
    inserts `finding_facts` first raises `IntegrityError` the moment FK enforcement is
    on, which is why this needs `enforce_foreign_keys=True` rather than trusting the
    insert order by inspection. A minimal plan, not `_full_plan()`: that one's
    `observed`/`gone` facts name findings no `new_findings` entry mints, standing in for
    ones an earlier delivery already recorded — fine with FK enforcement off, but a
    second, unrelated FK gap under strict enforcement this test is not about."""
    store, engine = _store_and_engine(tmp_path, enforce_foreign_keys=True)
    # `finding_sets.artifact_id` FKs to `artifacts` too — in production the routine's own
    # `produces:` step already wrote this artifact before delivery ever runs; seeded here
    # by hand only because this plan is built directly rather than through that path.
    with engine.begin() as conn:
        conn.execute(
            sa.insert(artifacts).values(
                artifact_id="art_fk",
                chunk_id="ch_1",
                node_id="nd_1",
                node_name="garden-survey",
                epoch=1,
                name="findings",
                kind="asset",
                data="{}",
                produced_at=_NOW,
            )
        )
    plan = DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="garden-survey",
        epoch=1,
        at=_NOW,
        run=_RUN,
        deltas=[
            DeltaMaterialization(
                finding_set=NewFindingSet(
                    finding_set_id="fins_fk",
                    artifact_id="art_fk",
                    scope_slug="blizzard",
                    revisions={"blizzard": "aaa1111"},
                    measurement=None,
                ),
                new_findings=[
                    NewFinding(
                        finding_id="fin_fk",
                        routine_name="nightly",
                        scope_slug="blizzard",
                        class_="stale-docstring",
                        locus="a.py:1",
                        summary="s",
                        introduced=None,
                        introduced_at=None,
                    )
                ],
                facts=[FindingFactRecord(finding_id="fin_fk", kind="add", finding_set_id="fins_fk", note=None)],
            )
        ],
        proposals=[],
    )

    outcome = store.deliver(plan)

    assert outcome is DeliveryOutcome.RECORDED


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
    """A second visit at a fresh (node_id, epoch) resolving the *same* already-
    materialized artifact must return ALREADY_RECORDED cleanly, not trip
    `finding_sets.artifact_id`'s unique constraint with a raw IntegrityError."""
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
        deltas=[
            DeltaMaterialization(
                finding_set=NewFindingSet(
                    finding_set_id="fins_replay",
                    artifact_id="art_placeholder",  # same artifact_id as `first`'s finding set
                    scope_slug="blizzard",
                    revisions={"blizzard": "abc1234"},
                    measurement="12.3s",
                )
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
        # This fresh visit still gets its own marker, though it inserted nothing else.
        assert len(marker_rows) == 2


def test_deliver_with_one_delta_already_materialized_still_lands_the_other(tmp_path: Path) -> None:
    """A visit resolving a *mix* of one already-materialized artifact (`A`) and one
    genuinely new one (`B`) must still land `B`'s rows — the broader idempotence check
    skips only `A`'s group, never bails the whole plan."""
    store, engine = _store_and_engine(tmp_path)
    first = DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="garden-survey",
        epoch=1,
        at=_NOW,
        run=_RUN,
        deltas=[
            DeltaMaterialization(
                finding_set=NewFindingSet(
                    finding_set_id="fins_a",
                    artifact_id="art_a",
                    scope_slug="blizzard",
                    revisions={"blizzard": "aaaaaaa"},
                    measurement="1.0s",
                ),
                new_findings=[
                    NewFinding(
                        finding_id="fin_a",
                        routine_name="nightly",
                        scope_slug="blizzard",
                        class_="stale-docstring",
                        locus="a.py:1",
                        summary="s_a",
                        introduced=None,
                        introduced_at=None,
                    )
                ],
                facts=[FindingFactRecord(finding_id="fin_a", kind="add", finding_set_id="fins_a", note=None)],
            )
        ],
        proposals=[],
    )
    assert store.deliver(first) is DeliveryOutcome.RECORDED

    second = DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_2",
        node_name="garden-survey",
        epoch=2,
        at=_NOW,
        run=_RUN,
        deltas=[
            DeltaMaterialization(
                finding_set=NewFindingSet(
                    finding_set_id="fins_a_replay",
                    artifact_id="art_a",  # already materialized under `first` — must be skipped
                    scope_slug="blizzard",
                    revisions={"blizzard": "aaaaaaa"},
                    measurement="1.0s",
                ),
                new_findings=[
                    NewFinding(
                        finding_id="fin_a_replay",
                        routine_name="nightly",
                        scope_slug="blizzard",
                        class_="stale-docstring",
                        locus="a.py:1",
                        summary="s_a",
                        introduced=None,
                        introduced_at=None,
                    )
                ],
                facts=[
                    FindingFactRecord(finding_id="fin_a_replay", kind="add", finding_set_id="fins_a_replay", note=None)
                ],
            ),
            DeltaMaterialization(
                finding_set=NewFindingSet(
                    finding_set_id="fins_b",
                    artifact_id="art_b",  # genuinely new
                    scope_slug="blizzard",
                    revisions={"blizzard": "bbbbbbb"},
                    measurement="2.0s",
                ),
                new_findings=[
                    NewFinding(
                        finding_id="fin_b",
                        routine_name="nightly",
                        scope_slug="blizzard",
                        class_="stale-docstring",
                        locus="b.py:1",
                        summary="s_b",
                        introduced=None,
                        introduced_at=None,
                    )
                ],
                facts=[FindingFactRecord(finding_id="fin_b", kind="add", finding_set_id="fins_b", note=None)],
            ),
        ],
        proposals=[],
    )

    outcome = store.deliver(second)

    assert outcome is DeliveryOutcome.RECORDED
    with engine.connect() as conn:
        finding_rows = conn.execute(sa.select(findings).order_by(findings.c.finding_id)).all()
        assert [r.finding_id for r in finding_rows] == ["fin_a", "fin_b"]

        fact_rows = conn.execute(sa.select(finding_facts).order_by(finding_facts.c.id)).all()
        assert [(r.finding_id, r.kind) for r in fact_rows] == [("fin_a", "add"), ("fin_b", "add")]

        set_rows = conn.execute(sa.select(finding_sets).order_by(finding_sets.c.finding_set_id)).all()
        assert [r.finding_set_id for r in set_rows] == ["fins_a", "fins_b"]
        assert [r.artifact_id for r in set_rows] == ["art_a", "art_b"]

        marker_rows = conn.execute(sa.select(artifacts).where(artifacts.c.name == "garden-delivered")).all()
        assert len(marker_rows) == 2


def test_deliver_at_a_new_epoch_resolving_the_same_proposal_artifact_mints_no_duplicate(tmp_path: Path) -> None:
    """The proposal twin of the delta idempotence test above: a fresh (node_id, epoch)
    re-carrying the same `--proposals` artifact must mint the proposal exactly once,
    keyed `(source_artifact_id, ref)`."""
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
        deltas=[],
        proposals=[
            NewProposal(
                proposal_id="gprop_replay",
                routine_name="nightly",
                class_="fix-the-source",
                title="Author a docstring standard",
                body="the case",
                source_artifact_id="art_docket",  # same (artifact, ref) pair as `first`'s proposal
                ref="p1",
                finding_ids=["fin_1"],
            )
        ],
    )

    outcome = store.deliver(second)

    assert outcome is DeliveryOutcome.ALREADY_RECORDED
    with engine.connect() as conn:
        proposal_rows = conn.execute(sa.select(garden_proposals)).all()
        assert [r.proposal_id for r in proposal_rows] == ["gprop_1"]
        link_rows = conn.execute(sa.select(garden_proposal_findings)).all()
        assert [(r.proposal_id, r.finding_id) for r in link_rows] == [("gprop_1", "fin_1")]
        marker_rows = conn.execute(sa.select(artifacts).where(artifacts.c.name == "garden-delivered")).all()
        assert len(marker_rows) == 2


def test_deliver_with_one_proposal_already_delivered_still_lands_the_other(tmp_path: Path) -> None:
    """The proposal twin of the mixed-survival delta test above: a visit re-carrying an
    already-delivered proposal (`p1`) alongside a new one (`p2`) must still land `p2`."""
    store, engine = _store_and_engine(tmp_path)
    assert store.deliver(_full_plan()) is DeliveryOutcome.RECORDED

    second = DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_2",
        node_name="garden-survey",
        epoch=2,
        at=_NOW,
        run=_RUN,
        deltas=[],
        proposals=[
            NewProposal(
                proposal_id="gprop_replay",
                routine_name="nightly",
                class_="fix-the-source",
                title="Author a docstring standard",
                body="the case",
                source_artifact_id="art_docket",
                ref="p1",  # already delivered
                finding_ids=["fin_1"],
            ),
            NewProposal(
                proposal_id="gprop_2",
                routine_name="nightly",
                class_="fix-the-source",
                title="A second candidate",
                body="the other case",
                source_artifact_id="art_docket",
                ref="p2",  # genuinely new
                finding_ids=["fin_1"],
            ),
        ],
    )

    outcome = store.deliver(second)

    assert outcome is DeliveryOutcome.RECORDED
    with engine.connect() as conn:
        proposal_rows = conn.execute(sa.select(garden_proposals).order_by(garden_proposals.c.proposal_id)).all()
        assert [r.proposal_id for r in proposal_rows] == ["gprop_1", "gprop_2"]


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
        deltas=[
            DeltaMaterialization(
                finding_set=NewFindingSet(
                    finding_set_id="fins_2",
                    artifact_id="art_clean",
                    scope_slug="blizzard",
                    revisions={},
                    measurement=None,
                )
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
