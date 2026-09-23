"""``GardenProposalStore`` — the garden-proposal repository (blizzard#390, component
tier). Migrated-to-head sqlite-on-disk — the ``tests/test_routine_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosureKind, GardenProposalItemOutcome
from blizzard.hub.domain.garden_proposals import GardenProposalCounts
from blizzard.hub.domain.work import WorkRef
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_closure_store import insert_garden_proposal_closure_row
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import count_queries, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _pass(engine: Engine, proposal_id: str, *, at: datetime = _NOW) -> None:
    store_connections = hub_store_connections(engine)
    with store_connections.write("seed_pass") as conn:
        insert_garden_proposal_closure_row(
            conn,
            proposal_id=proposal_id,
            closure=GardenProposalClosureKind.PASSED,
            reason="not worth it",
            closed_by="operator",
            at=at,
            item_outcome=None,
            pointer=None,
        )


def _accept_decline(engine: Engine, proposal_id: str, *, at: datetime = _NOW) -> None:
    store_connections = hub_store_connections(engine)
    with store_connections.write("seed_accept_decline") as conn:
        insert_garden_proposal_closure_row(
            conn,
            proposal_id=proposal_id,
            closure=GardenProposalClosureKind.ACCEPTED,
            reason=None,
            closed_by="operator",
            at=at,
            item_outcome=GardenProposalItemOutcome.DECLINED,
            pointer=None,
        )


def _accept_mint(engine: Engine, proposal_id: str, *, at: datetime = _NOW) -> None:
    store_connections = hub_store_connections(engine)
    with store_connections.write("seed_accept_mint") as conn:
        insert_garden_proposal_closure_row(
            conn,
            proposal_id=proposal_id,
            closure=GardenProposalClosureKind.ACCEPTED,
            reason=None,
            closed_by="operator",
            at=at,
            item_outcome=GardenProposalItemOutcome.MINTED,
            pointer=WorkRef(source="hub", ref=proposal_id),
        )


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


def test_counts_by_class_groups_by_routine_and_class(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(
        "gprop_1", routine_name="nightly", class_="fix-the-source", title="t1", body="b", findings=["fin_1"], at=_NOW
    )
    store.create(
        "gprop_2", routine_name="nightly", class_="fix-the-source", title="t2", body="b", findings=["fin_2"], at=_NOW
    )
    store.create("gprop_3", routine_name="nightly", class_="wontfix", title="t3", body="b", findings=["fin_1"], at=_NOW)
    store.create(
        "gprop_4",
        routine_name="other-routine",
        class_="fix-the-source",
        title="t4",
        body="b",
        findings=["fin_1"],
        at=_NOW,
    )

    rows = store.counts_by_class(since=_NOW - timedelta(hours=1), until=_NOW + timedelta(hours=1))

    assert rows == [
        GardenProposalCounts(
            routine_name="nightly",
            class_="fix-the-source",
            open=2,
            passed=0,
            accepted_with_item=0,
            accepted_without_item=0,
        ),
        GardenProposalCounts(
            routine_name="nightly", class_="wontfix", open=1, passed=0, accepted_with_item=0, accepted_without_item=0
        ),
        GardenProposalCounts(
            routine_name="other-routine",
            class_="fix-the-source",
            open=1,
            passed=0,
            accepted_with_item=0,
            accepted_without_item=0,
        ),
    ]
    assert [r.created for r in rows] == [2, 1, 1]


def test_counts_by_class_since_is_inclusive_and_until_is_exclusive(tmp_path: Path) -> None:
    store = _store(tmp_path)
    since = _NOW
    until = _NOW + timedelta(hours=1)
    store.create(
        "gprop_at_since", routine_name="nightly", class_="c", title="t1", body="b", findings=["fin_1"], at=since
    )
    store.create(
        "gprop_at_until", routine_name="nightly", class_="c", title="t2", body="b", findings=["fin_2"], at=until
    )

    rows = store.counts_by_class(since=since, until=until)

    assert rows == [
        GardenProposalCounts(
            routine_name="nightly", class_="c", open=1, passed=0, accepted_with_item=0, accepted_without_item=0
        )
    ]


def test_counts_by_class_splits_minted_and_declined_accepts(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    store.create("gprop_open", routine_name="nightly", class_="c", title="t1", body="b", findings=["fin_1"], at=_NOW)
    store.create("gprop_passed", routine_name="nightly", class_="c", title="t2", body="b", findings=["fin_2"], at=_NOW)
    store.create("gprop_minted", routine_name="nightly", class_="c", title="t3", body="b", findings=["fin_1"], at=_NOW)
    store.create(
        "gprop_declined", routine_name="nightly", class_="c", title="t4", body="b", findings=["fin_2"], at=_NOW
    )
    _pass(engine, "gprop_passed")
    _accept_mint(engine, "gprop_minted")
    _accept_decline(engine, "gprop_declined")

    rows = store.counts_by_class(since=_NOW - timedelta(hours=1), until=_NOW + timedelta(hours=1))

    assert rows == [
        GardenProposalCounts(
            routine_name="nightly", class_="c", open=1, passed=1, accepted_with_item=1, accepted_without_item=1
        )
    ]
    assert rows[0].created == 4


def test_counts_by_class_is_empty_for_a_routine_with_no_proposals_in_window(tmp_path: Path) -> None:
    store = _store(tmp_path)

    rows = store.counts_by_class(since=_NOW - timedelta(hours=1), until=_NOW + timedelta(hours=1))

    assert rows == []


def test_counts_by_class_routine_name_filter_narrows_to_one_routine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("gprop_1", routine_name="nightly", class_="c", title="t1", body="b", findings=["fin_1"], at=_NOW)
    store.create("gprop_2", routine_name="other-routine", class_="c", title="t2", body="b", findings=["fin_2"], at=_NOW)

    rows = store.counts_by_class(
        since=_NOW - timedelta(hours=1), until=_NOW + timedelta(hours=1), routine_name="nightly"
    )

    assert rows == [
        GardenProposalCounts(
            routine_name="nightly", class_="c", open=1, passed=0, accepted_with_item=0, accepted_without_item=0
        )
    ]


def test_counts_by_class_query_count_is_flat_regardless_of_proposal_count(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine = _sized_store(tmp_path / "small", 5)
    large, large_engine = _sized_store(tmp_path / "large", 15)  # 3x the small fixture

    since, until = _NOW - timedelta(hours=1), _NOW + timedelta(hours=1)
    small_count = count_queries(small_engine, lambda: small.counts_by_class(since=since, until=until))
    large_count = count_queries(large_engine, lambda: large.counts_by_class(since=since, until=until))

    assert len(small.counts_by_class(since=since, until=until)) > 0
    assert small_count == large_count


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
