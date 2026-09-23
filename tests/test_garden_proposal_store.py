"""``GardenProposalStore`` — the garden-proposal repository (blizzard#390, component
tier). Migrated-to-head sqlite-on-disk — the ``tests/test_routine_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import count_queries, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store_and_engine(tmp_path: Path) -> tuple[GardenProposalStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO scopes (slug, description, created_at) VALUES ('blizzard', '', :now)"),
            {"now": _NOW},
        )
    store_connections = hub_store_connections(engine)
    findings = FindingStore(store_connections)
    findings.add(
        "fin_1",
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s1",
        introduced=None,
        at=_NOW,
    )
    findings.add(
        "fin_2",
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="b.py:2",
        summary="s2",
        introduced=None,
        at=_NOW,
    )
    return GardenProposalStore(store_connections), engine


def _store(tmp_path: Path) -> GardenProposalStore:
    store, _ = _store_and_engine(tmp_path)
    return store


def _sized_store(tmp_path: Path, n: int) -> tuple[GardenProposalStore, Engine]:
    """``n`` proposals, each with its own finding — the fixture a query-count-parity
    test seeds at two different sizes."""
    store, engine = _store_and_engine(tmp_path)
    findings = FindingStore(hub_store_connections(engine))
    for i in range(n):
        findings.add(
            f"fin_extra_{i}",
            routine_name="nightly",
            scope_slug="blizzard",
            class_="stale-docstring",
            locus=f"x{i}.py:1",
            summary=f"s{i}",
            introduced=None,
            at=_NOW,
        )
        store.create(
            f"gprop_extra_{i}",
            routine_name="nightly",
            class_="c",
            title=f"t{i}",
            body="b",
            findings=[f"fin_extra_{i}"],
            at=_NOW,
        )
    return store, engine


def test_create_then_get_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)

    created = store.create(
        "gprop_1",
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        findings=["fin_1", "fin_2"],
        at=_NOW,
    )

    fetched = store.get("gprop_1")
    assert fetched == created
    assert set(fetched.findings) == {"fin_1", "fin_2"}  # type: ignore[union-attr]


def test_create_with_no_findings_writes_no_link_rows(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)

    created = store.create(
        "gprop_1", routine_name="nightly", class_="fix-the-source", title="t", body="b", findings=[], at=_NOW
    )

    assert created.findings == []
    fetched = store.get("gprop_1")
    assert fetched is not None
    assert fetched.findings == []
    with engine.begin() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM garden_proposal_findings WHERE proposal_id = 'gprop_1'")
        ).scalar_one()
    assert count == 0


def test_get_unknown_id_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get("gprop_ghost") is None


def test_list_all_orders_newest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("gprop_old", routine_name="nightly", class_="c", title="old", body="b", findings=["fin_1"], at=_NOW)
    store.create(
        "gprop_new",
        routine_name="nightly",
        class_="c",
        title="new",
        body="b",
        findings=["fin_2"],
        at=_NOW.replace(hour=13),
    )

    ids = [p.proposal_id for p in store.list_all()]

    assert ids == ["gprop_new", "gprop_old"]


def test_list_for_routine_orders_newest_first_and_excludes_other_routines(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("gprop_old", routine_name="nightly", class_="c", title="old", body="b", findings=["fin_1"], at=_NOW)
    store.create(
        "gprop_new",
        routine_name="nightly",
        class_="c",
        title="new",
        body="b",
        findings=["fin_2"],
        at=_NOW.replace(hour=13),
    )
    store.create(
        "gprop_other", routine_name="other-routine", class_="c", title="other", body="b", findings=["fin_1"], at=_NOW
    )

    ids = [p.proposal_id for p in store.list_for_routine("nightly")]

    assert ids == ["gprop_new", "gprop_old"]


def test_list_for_routine_is_empty_for_an_unseen_routine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("gprop_1", routine_name="nightly", class_="c", title="t", body="b", findings=["fin_1"], at=_NOW)

    assert store.list_for_routine("ghost-routine") == []


def test_count_by_class_counts_across_the_named_routine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(
        "gprop_1", routine_name="nightly", class_="fix-the-source", title="t1", body="b", findings=["fin_1"], at=_NOW
    )
    store.create(
        "gprop_2", routine_name="nightly", class_="fix-the-source", title="t2", body="b", findings=["fin_2"], at=_NOW
    )
    store.create("gprop_3", routine_name="nightly", class_="wontfix", title="t3", body="b", findings=["fin_1"], at=_NOW)

    assert store.count_by_class("nightly", "fix-the-source") == 2
    assert store.count_by_class("nightly", "wontfix") == 1
    assert store.count_by_class("nightly", "unseen-class") == 0


def test_two_proposals_with_overlapping_findings_stay_distinguished(tmp_path: Path) -> None:
    """The link table is per-proposal, so two proposals naming an overlapping finding
    never collapse into one row set (D7)."""
    store = _store(tmp_path)
    store.create(
        "gprop_1", routine_name="nightly", class_="c", title="t1", body="b", findings=["fin_1", "fin_2"], at=_NOW
    )
    store.create("gprop_2", routine_name="nightly", class_="c", title="t2", body="b", findings=["fin_1"], at=_NOW)

    assert set(store.get("gprop_1").findings) == {"fin_1", "fin_2"}  # type: ignore[union-attr]
    assert set(store.get("gprop_2").findings) == {"fin_1"}  # type: ignore[union-attr]


def test_list_all_and_list_for_routine_query_count_is_independent_of_proposal_count(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine = _sized_store(tmp_path / "small", 3)
    large, large_engine = _sized_store(tmp_path / "large", 9)  # 3x the small fixture

    small_all_count = count_queries(small_engine, small.list_all)
    large_all_count = count_queries(large_engine, large.list_all)
    small_routine_count = count_queries(small_engine, lambda: small.list_for_routine("nightly"))
    large_routine_count = count_queries(large_engine, lambda: large.list_for_routine("nightly"))

    assert len(small.list_all()) == 3
    assert len(large.list_all()) == 9
    assert small_all_count == large_all_count
    assert small_routine_count == large_routine_count
