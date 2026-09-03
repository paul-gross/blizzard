"""The ``finding_facts.ref`` revision against a store that already holds delivered
findings — additive and never backfilled, so every fact recorded before it reads back a
null ref. Seeded with literal ``sa.Table`` shapes rather than importing ``schema.py``,
which already carries the column this revision adds — the
``tests/test_finding_exits_migration.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
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
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.schema import findings as findings_table
from blizzard.hub.store.schema import garden_proposal_findings
from tests.support import hub_store_connections, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_BEFORE = "20260902_0900_chunk_dependencies"  # the head just before finding_facts.ref
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_RUN = RunContext(routine_name="nightly", scope_slug="blizzard", mode="dry_run")

_SCOPES = sa.Table(
    "scopes",
    sa.MetaData(),
    sa.Column("slug", sa.String, primary_key=True),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

_ARTIFACTS = sa.Table(
    "artifacts",
    sa.MetaData(),
    sa.Column("artifact_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("node_name", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("data", sa.Text, nullable=False),
    sa.Column("produced_at", sa.DateTime, nullable=False),
)

_FINDINGS = sa.Table(
    "findings",
    sa.MetaData(),
    sa.Column("finding_id", sa.String, primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("scope_slug", sa.String, nullable=False),
    sa.Column("class", sa.String, key="class_", nullable=False),
    sa.Column("locus", sa.String, nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
)

_FINDING_SETS = sa.Table(
    "finding_sets",
    sa.MetaData(),
    sa.Column("finding_set_id", sa.String, primary_key=True),
    sa.Column("artifact_id", sa.String, nullable=False),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("scope_slug", sa.String, nullable=False),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("revisions", sa.Text, nullable=False),
    sa.Column("measurement", sa.Text, nullable=True),
)

# The pre-ref shape: an `add` fact carries no ref column to record one in.
_OLD_FINDING_FACTS = sa.Table(
    "finding_facts",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("finding_id", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("recorded_at", sa.DateTime, nullable=False),
    sa.Column("finding_set_id", sa.String, nullable=True),
)


def _seed_delivered_finding(tmp_path: Path) -> sa.Engine:
    """A store at ``_BEFORE`` holding one delivered finding, then upgraded to head."""
    runner, engine = migrate_to(tmp_path, _BEFORE)
    with engine.begin() as conn:
        conn.execute(sa.insert(_SCOPES).values(slug="blizzard", description="", created_at=_T0))
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
        conn.execute(
            sa.insert(_ARTIFACTS).values(
                artifact_id="art_a",
                chunk_id="ch_1",
                node_id="nd_1",
                node_name="garden-survey",
                epoch=1,
                name="findings",
                kind="asset",
                data="{}",
                produced_at=_T0,
            )
        )
        conn.execute(
            sa.insert(_FINDINGS).values(
                finding_id="fin_a",
                routine_name="nightly",
                scope_slug="blizzard",
                class_="stale-docstring",
                locus="a.py:1",
                summary="s_a",
            )
        )
        conn.execute(
            sa.insert(_FINDING_SETS).values(
                finding_set_id="fins_a",
                artifact_id="art_a",
                chunk_id="ch_1",
                scope_slug="blizzard",
                routine_name="nightly",
                revisions="{}",
                measurement=None,
            )
        )
        conn.execute(
            sa.insert(_OLD_FINDING_FACTS).values(
                finding_id="fin_a", kind="add", recorded_at=_T0, finding_set_id="fins_a"
            )
        )

    runner.upgrade("head")
    return create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")


def _republish_plan() -> DeliveryPlan:
    """The lost-response replay: the same docket artifact resolved again at a fresh
    (node, epoch), its `add` op minting a fresh id the store will never insert, and a
    proposal citing that op's ref."""
    return DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_2",
        node_name="garden-survey",
        epoch=2,
        at=_T0,
        run=_RUN,
        deltas=[
            DeltaMaterialization(
                finding_set=NewFindingSet(
                    finding_set_id="fins_a_replay",
                    artifact_id="art_a",  # already materialized before the upgrade — dropped
                    scope_slug="blizzard",
                    revisions={"blizzard": "aaaaaaa"},
                    measurement=None,
                ),
                new_findings=[
                    NewFinding(
                        finding_id="fin_a_phantom",
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
                    FindingFactRecord(finding_id="fin_a_phantom", kind="add", finding_set_id="fins_a_replay", ref="F1")
                ],
            )
        ],
        proposals=[
            NewProposal(
                proposal_id="gprop_1",
                routine_name="nightly",
                class_="fix-the-source",
                title="Republished docket",
                body="the case",
                source_artifact_id="art_docket_1",
                ref="p1",
                finding_ids=["fin_a_phantom"],
            )
        ],
    )


def test_upgrade_leaves_preexisting_add_facts_null_and_still_delivers(tmp_path: Path) -> None:
    """Additive, no backfill: the fact recorded before the column reads back a null ref,
    and the store still writes new facts with theirs."""
    engine = _seed_delivered_finding(tmp_path)

    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT finding_id, ref FROM finding_facts")).all()
    assert [(r.finding_id, r.ref) for r in rows] == [("fin_a", None)]


def test_republish_over_a_preref_finding_set_drops_the_unresolvable_citation(tmp_path: Path) -> None:
    """A delta materialized before the ref column recorded no ref to resolve a replay's
    citation against. The delivery must still land, and the citation of the id this visit
    minted but never inserted must be dropped, not written as a link to nothing."""
    engine = _seed_delivered_finding(tmp_path)
    store = GardenDeliveryStore(hub_store_connections(engine))

    assert store.deliver(_republish_plan()) is DeliveryOutcome.RECORDED

    with engine.connect() as conn:
        assert {r.finding_id for r in conn.execute(sa.select(findings_table))} == {"fin_a"}
        assert conn.execute(sa.select(garden_proposal_findings)).all() == []
