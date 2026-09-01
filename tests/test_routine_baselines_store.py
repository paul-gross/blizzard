"""``RoutineBaselineService`` over real stores (blizzard#392 D1, D5, component tier):
a delivered garden finding set and a chunk landing after it, joined purely on the repo
names each side independently holds — the one join D1 rests on."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import Id
from blizzard.hub.domain.routine_baselines import RepoLandings, RoutineBaselineService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingSetStore
from tests.support import chunk_stores, hub_store_connections, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed_artifact(conn, artifact_id: str, *, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        insert(s.artifacts).values(
            artifact_id=artifact_id,
            chunk_id=chunk_id,
            node_id="nd_1",
            node_name="survey",
            epoch=1,
            name="findings-blizzard",
            kind="asset",
            data="[]",
            produced_at=_NOW,
        )
    )


def test_landed_since_joins_the_finding_sets_repo_names_against_delivery(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
        seed_chunk(conn, "ch_2", graph_id="gr_1", at=_NOW)
        conn.execute(insert(s.scopes).values(slug="blizzard", description="", created_at=_NOW))
        _seed_artifact(conn, "art_1", chunk_id="ch_1")

    baseline_at = _NOW
    finding_set_id = Id.mint_at("fins", baseline_at).value
    FindingSetStore(hub_store_connections(engine)).create(
        finding_set_id,
        artifact_id="art_1",
        chunk_id="ch_1",
        scope_slug="blizzard",
        routine_name="gardening",
        revisions={"blizzard": "a1b2c3d"},
        measurement=None,
    )
    delivery = chunk_stores(engine, FixedClock(instant=_NOW)).delivery
    delivery.record_delivery_repo_landed(
        "ch_2", repo="blizzard", commit_hash="e4f5", at=baseline_at + timedelta(hours=1)
    )
    delivery.record_delivery_repo_landed(
        "ch_2", repo="other-repo", commit_hash="9988", at=baseline_at + timedelta(hours=1)
    )

    service = RoutineBaselineService(finding_sets=FindingSetStore(hub_store_connections(engine)), delivery=delivery)
    baselines = service.baselines_for("gardening")

    assert len(baselines) == 1
    baseline = baselines[0]
    assert baseline.finding_set_id == finding_set_id
    assert baseline.recorded_at == baseline_at
    assert baseline.repos == [RepoLandings(repo="blizzard", revision="a1b2c3d", landed_since=1)]


def test_a_never_swept_routine_yields_no_baselines(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)

    delivery = chunk_stores(engine, FixedClock(instant=_NOW)).delivery
    service = RoutineBaselineService(finding_sets=FindingSetStore(hub_store_connections(engine)), delivery=delivery)

    assert service.baselines_for("gardening") == []
