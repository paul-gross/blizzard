"""``FindingStore.list_page``'s SQL liveness prefilter (component tier).

``bzh:page-bounded-read`` / ``bzh:live-set-read`` — the live set the query keeps equals
``derive_liveness``'s for every fact shape, and the page's cost stays flat when only the
exited-finding count grows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.garden.findings.model import EXIT_KINDS, FACT_KINDS, FindingFact, derive_liveness
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from tests.support import (
    count_queries,
    count_rows_read,
    default_project,
    default_tenant,
    hub_store_connections,
    migrated_hub_at,
    stamp,
)

pytestmark = pytest.mark.component

_T0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _store_and_engine(tmp_path: Path) -> tuple[FindingStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO scopes (scope_id, slug, description, created_at, tenant_id, project_id)"
                " VALUES ('scp_blizzard', 'blizzard', '', :now, :tenant, :project)"
            ),
            {"now": _T0, "tenant": default_tenant(engine), "project": default_project(engine)},
        )
    return FindingStore(hub_store_connections(engine)), engine


def _finding(engine: Engine, finding_id: str, facts: list[tuple[str, int]], *, annotate: bool = False) -> None:
    """Insert a finding and raw facts as ``(kind, recorded_at seconds)``, in insertion order;
    ``annotate`` gives every fact other than ``add``/``observed`` a note and an actor."""
    with engine.begin() as conn:
        conn.execute(
            insert(s.findings).values(
                stamp(
                    s.findings,
                    finding_id=finding_id,
                    routine_name="nightly",
                    scope_slug="blizzard",
                    class_="c",
                    locus="l",
                    summary="x",
                    introduced=None,
                    introduced_at=_T0,
                    source="routine",
                )
            )
        )
        for kind, seconds in facts:
            human = annotate and kind not in ("add", "observed")
            conn.execute(
                insert(s.finding_facts).values(
                    stamp(
                        s.finding_facts,
                        finding_id=finding_id,
                        kind=kind,
                        recorded_at=_at(seconds),
                        note="why" if human else None,
                        actor="pat" if human else None,
                    )
                )
            )


def _fixtures() -> dict[str, list[tuple[str, int]]]:
    fixtures: dict[str, list[tuple[str, int]]] = {}
    for kind in sorted(FACT_KINDS):
        fixtures[f"newest_{kind}"] = [("add", 0), (kind, 10)]
    fixtures["exit_then_reopened"] = [("add", 0), ("wont-fix", 5), ("reopened", 10)]
    fixtures["gone_then_observed"] = [("add", 0), ("gone", 5), ("observed", 10)]
    fixtures["delivered_then_observed"] = [("add", 0), ("delivered", 5), ("observed", 10)]
    fixtures["tie_later_inserted_exit"] = [("add", 0), ("observed", 5), ("gone", 5)]
    fixtures["tie_later_inserted_live"] = [("add", 0), ("gone", 5), ("observed", 5)]
    fixtures["out_of_order_older_exit_loses"] = [("add", 0), ("observed", 10), ("resolved", 5)]
    fixtures["out_of_order_older_live_loses"] = [("add", 10), ("resolved", 20), ("observed", 5)]
    fixtures["no_facts"] = []
    return fixtures


def test_sql_live_set_equals_derive_liveness(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    fixtures = _fixtures()
    for finding_id, facts in fixtures.items():
        _finding(engine, finding_id, facts)

    page = store.list_page(
        project_id=project, routine_name=None, scope_slug=None, include_gone=False, limit=len(fixtures) + 1
    )
    everything = store.list_page(
        project_id=project, routine_name=None, scope_slug=None, include_gone=True, limit=len(fixtures) + 1
    )

    assert page.next_cursor is None
    assert len(everything.findings) == len(fixtures)
    expected = [f.finding_id for f in everything.findings if f.live]
    assert [f.finding_id for f in page.findings] == expected
    assert {f.finding_id for f in everything.findings if f.live} == {
        fid for fid, facts in fixtures.items() if not facts or derive_liveness(_as_facts(facts)).live
    }
    assert "no_facts" in expected
    assert "out_of_order_older_exit_loses" in expected
    assert "tie_later_inserted_exit" not in expected


def _as_facts(facts: list[tuple[str, int]]):  #
    return [FindingFact(kind=kind, recorded_at=_at(seconds)) for kind, seconds in facts]


def _seed_interleaved(engine: Engine, *, exited_each_gap: int) -> None:
    for live_index in range(4):
        for gap in range(exited_each_gap):
            _finding(engine, f"fin_{live_index}_{gap}_x", [("add", 0), ("wont-fix", 1)])
        _finding(engine, f"fin_{live_index}_live", [("add", 0)])
    for gap in range(exited_each_gap):
        _finding(engine, f"fin_9_{gap}_x", [("add", 0), ("wont-fix", 1)])


def test_page_cost_is_flat_in_exited_finding_count(tmp_path: Path) -> None:
    measured = []
    for gaps in (1, 12):
        (tmp_path / f"g{gaps}").mkdir()
        store, engine = _store_and_engine(tmp_path / f"g{gaps}")
        project = default_project(engine)
        _seed_interleaved(engine, exited_each_gap=gaps)

        def read(store: FindingStore = store, project: str = project):  #
            return store.list_page(project_id=project, routine_name="nightly", scope_slug="blizzard", limit=2)

        first = read()
        measured.append(
            (
                count_queries(engine, read),
                count_rows_read(engine, read),
                [f.finding_id.split("_")[1] for f in first.findings],
                first.next_cursor is not None,
            )
        )
    assert measured[0][0] == measured[1][0]
    assert measured[0][1] == measured[1][1]
    assert measured[0][2:] == measured[1][2:]


def test_exclude_exited_keeps_exactly_the_findings_derive_liveness_does_not_exit(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    fixtures = _fixtures()
    for finding_id, facts in fixtures.items():
        _finding(engine, finding_id, facts)

    kept = store.list_for_routine(project, "nightly", include_gone=True, exclude_exited=True)
    everything = store.list_for_routine(project, "nightly", include_gone=True)

    assert len(everything) == len(fixtures)
    assert [f.finding_id for f in kept] == [f.finding_id for f in everything if not f.exited]
    assert {f.finding_id for f in kept} == {
        fid for fid, facts in fixtures.items() if not facts or derive_liveness(_as_facts(facts)).state not in EXIT_KINDS
    }
    assert [f.finding_id for f in store.list_by_source(project, scope_slug="blizzard", source="routine")] == [
        f.finding_id for f in everything if f.live
    ]
    assert [
        f.finding_id
        for f in store.list_by_source(
            project, scope_slug="blizzard", source="routine", include_gone=True, exclude_exited=True
        )
    ] == [f.finding_id for f in kept]


def test_bucket_read_cost_is_flat_in_exited_finding_count(tmp_path: Path) -> None:
    measured = []
    for exited in (1, 12):
        (tmp_path / f"e{exited}").mkdir()
        store, engine = _store_and_engine(tmp_path / f"e{exited}")
        project = default_project(engine)
        for index in range(exited):
            _finding(engine, f"fin_x{index}", [("add", 0), *[("observed", n) for n in range(1, 8)], ("wont-fix", 9)])
        _finding(engine, "fin_live", [("add", 0)])
        _finding(engine, "fin_gone", [("add", 0), ("gone", 1)])

        def read(store: FindingStore = store, project: str = project):  #
            return store.list_for_routine(project, "nightly", include_gone=True, exclude_exited=True)

        measured.append((count_queries(engine, read), count_rows_read(engine, read), [f.finding_id for f in read()]))
    assert measured[0] == measured[1]


def test_bounded_read_derives_what_the_full_chain_does(tmp_path: Path) -> None:
    """Every `FindingLiveness` field of a `Finding` from `list_page` and `get_many` equals
    `derive_liveness` over the chain `get_with_facts` returns."""
    store, engine = _store_and_engine(tmp_path)
    fixtures = _fixtures()
    fixtures["many_observed"] = [("add", 0), *[("observed", n) for n in range(1, 40)], ("gone", 50)]
    fixtures["noted_exit_then_reopened"] = [("add", 0), ("observed", 3), ("wont-fix", 5), ("reopened", 10)]
    for fid, facts in fixtures.items():
        _finding(engine, fid, facts, annotate=True)

    paged = {
        f.finding_id: f
        for f in store.list_page(
            project_id=None, routine_name=None, scope_slug=None, include_gone=True, limit=100
        ).findings
    }
    many = store.get_many(list(fixtures))

    assert set(paged) == set(many) == set(fixtures)
    for fid in fixtures:
        loaded = store.get_with_facts(fid)
        assert loaded is not None
        _, chain = loaded
        expected = derive_liveness(chain)
        for found in (paged[fid], many[fid], store.get(fid)):
            assert found is not None
            assert (found.state, found.live, found.note, found.actor) == (
                expected.state,
                expected.live,
                expected.note,
                expected.actor,
            )
            assert (found.first_observed_at, found.last_seen_at, found.observed_count) == (
                expected.first_observed_at,
                expected.last_seen_at,
                expected.observed_count,
            )


def test_page_read_cost_is_flat_in_observation_depth(tmp_path: Path) -> None:
    measured = []
    for depth in (1, 30):
        (tmp_path / f"d{depth}").mkdir()
        store, engine = _store_and_engine(tmp_path / f"d{depth}")
        for index in range(3):
            _finding(engine, f"fin_{index}", [("add", 0), *[("observed", n) for n in range(1, depth + 1)]])

        def read(store: FindingStore = store):  #
            return store.list_page(
                project_id=None, routine_name=None, scope_slug=None, include_gone=True, limit=10
            ).findings

        measured.append((count_queries(engine, read), count_rows_read(engine, read), [f.finding_id for f in read()]))
    assert measured[0] == measured[1]
