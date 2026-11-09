"""``EnvTimelineStore`` over a migrated sqlite-on-disk store (component tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.env_timeline_store import EnvTimelineStore
from tests.support.scaffolding import (
    count_queries,
    default_tenant,
    migrate_to,
    scoped_connections,
    seed_chunk,
    seed_graph,
    seed_project,
    stamp,
)

_T0 = datetime(2026, 3, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _insert(engine: Engine, table: sa.Table, **values: Any) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(table).values(stamp(table, **values)))


def _store(tmp_path: Path, *chunk_ids: str) -> tuple[EnvTimelineStore, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        for chunk_id in chunk_ids:
            seed_chunk(conn, chunk_id, graph_id="gr_1", at=_T0)
    return EnvTimelineStore(scoped_connections(engine, default_tenant(engine))), engine


def _route(
    engine: Engine,
    chunk_id: str,
    minutes: int,
    *,
    seq: int = 1,
    envs: tuple[str, ...] = ("env_a",),
    runner: str = "rn_1",
) -> str:
    route_id = f"rt_{chunk_id}_{seq}"
    _insert(
        engine,
        s.route_created,
        route_id=route_id,
        chunk_id=chunk_id,
        runner_id=runner,
        workspace_id="w1",
        created_at=_at(minutes),
        seq=seq,
    )
    for env in envs:
        _insert(engine, s.route_environments, route_id=route_id, environment_id=env)
    return route_id


def _release(engine: Engine, chunk_id: str, minutes: int, *, seq: int) -> None:
    _insert(engine, s.route_released, chunk_id=chunk_id, released_at=_at(minutes), seq=seq)


def _chunks_of(store: EnvTimelineStore, **kwargs: Any) -> list[str]:
    created, _ = store.routes(**{"since": None, "until": None, **kwargs})
    return sorted(c.chunk_id for c in created)


def test_routes_pair_environments_in_insertion_order_with_the_chunks_releases(tmp_path: Path) -> None:
    store, engine = _store(tmp_path, "ch_1")
    _route(engine, "ch_1", 1, envs=("env_b", "env_a"))
    _release(engine, "ch_1", 5, seq=2)

    created, released = store.routes(since=None, until=None)

    (route,) = created
    assert (route.runner_id, route.workspace_id, route.created_at, route.seq) == ("rn_1", "w1", _at(1), 1)
    assert route.environment_ids == ("env_b", "env_a")
    assert [(r.chunk_id, r.released_at, r.seq) for r in released] == [("ch_1", _at(5), 2)]


def test_since_drops_a_route_released_at_or_before_it_and_keeps_one_released_after(tmp_path: Path) -> None:
    store, engine = _store(tmp_path, "ch_1", "ch_2")
    _route(engine, "ch_1", 1)
    _release(engine, "ch_1", 5, seq=2)
    _route(engine, "ch_2", 1)
    _release(engine, "ch_2", 6, seq=2)

    assert _chunks_of(store, since=_at(5)) == ["ch_2"]


def test_since_drops_a_route_whose_chunk_completed_or_stopped_before_it(tmp_path: Path) -> None:
    store, engine = _store(tmp_path, "ch_done", "ch_stop", "ch_open")
    for chunk_id in ("ch_done", "ch_stop", "ch_open"):
        _route(engine, chunk_id, 1)
    _insert(engine, s.chunk_completed, chunk_id="ch_done", completed_at=_at(3), completed_by="u")
    _insert(engine, s.chunk_stopped, chunk_id="ch_stop", stopped_at=_at(3), stopped_by="u")

    assert _chunks_of(store, since=_at(10)) == ["ch_open"]


def test_since_drops_a_route_whose_chunk_reached_the_terminal_node_before_it(tmp_path: Path) -> None:
    store, engine = _store(tmp_path, "ch_1", "ch_2")
    _route(engine, "ch_1", 1)
    _route(engine, "ch_2", 1)
    for chunk_id, to_node in (("ch_1", "done"), ("ch_2", "nd_2")):
        _insert(
            engine,
            s.transitions,
            transition_id=f"tr_{chunk_id}",
            chunk_id=chunk_id,
            graph_id="gr_1",
            to_node_id=to_node,
            epoch=1,
            runner_id="rn_1",
            recorded_at=_at(3),
        )

    assert _chunks_of(store, since=_at(10)) == ["ch_2"]


def test_since_keeps_a_route_created_after_the_chunk_ended_as_a_restart(tmp_path: Path) -> None:
    store, engine = _store(tmp_path, "ch_1")
    _route(engine, "ch_1", 1)
    _insert(engine, s.chunk_stopped, chunk_id="ch_1", stopped_at=_at(3), stopped_by="u")
    _route(engine, "ch_1", 5, seq=2)

    created, _ = store.routes(since=_at(10), until=None)

    assert [c.seq for c in created] == [2]


def test_until_excludes_routes_created_at_or_after_it_and_none_returns_all(tmp_path: Path) -> None:
    store, engine = _store(tmp_path, "ch_1", "ch_2", "ch_3")
    for n, chunk_id in enumerate(("ch_1", "ch_2", "ch_3")):
        _route(engine, chunk_id, n + 1)

    assert _chunks_of(store, until=_at(3)) == ["ch_1", "ch_2"]
    assert _chunks_of(store, until=None) == ["ch_1", "ch_2", "ch_3"]


def test_routes_narrow_to_one_chunk_or_one_project(tmp_path: Path) -> None:
    store, engine = _store(tmp_path, "ch_1", "ch_2")
    other = seed_project(engine, "other")
    _insert(engine, s.chunks, chunk_id="ch_3", graph_id="gr_1", minted_at=_T0, project_id=other)
    for chunk_id in ("ch_1", "ch_2", "ch_3"):
        _route(engine, chunk_id, 1)

    assert _chunks_of(store, chunk_id="ch_2") == ["ch_2"]
    assert _chunks_of(store, project=other) == ["ch_3"]


def test_chunk_facts_reads_transitions_and_migrations(tmp_path: Path) -> None:
    store, engine = _store(tmp_path, "ch_1")
    _insert(
        engine,
        s.transitions,
        transition_id="tr_1",
        chunk_id="ch_1",
        graph_id="gr_1",
        from_node_id="nd_1",
        to_node_id="nd_2",
        choice_name="ok",
        epoch=3,
        runner_id="rn_1",
        recorded_at=_at(2),
    )
    _insert(
        engine,
        s.chunk_migrations,
        migration_id="mg_1",
        chunk_id="ch_1",
        from_node_id="nd_2",
        from_graph_id="gr_1",
        to_graph_id="gr_2",
        landed_node_id="nd_9",
        choice_name="move",
        epoch=4,
        recorded_at=_at(4),
    )
    _insert(
        engine,
        s.chunk_migrations,
        migration_id="mg_2",
        chunk_id="ch_1",
        from_node_id="nd_9",
        from_graph_id="gr_2",
        to_graph_id="gr_1",
        landed_node_id=None,
        choice_name="back",
        epoch=5,
        recorded_at=_at(6),
    )

    facts = store.chunk_facts(["ch_1"])["ch_1"]

    (plain,) = facts.transitions
    assert (plain.graph_id, plain.from_node_id, plain.to_node_id, plain.choice_name, plain.epoch) == (
        "gr_1",
        "nd_1",
        "nd_2",
        "ok",
        3,
    )
    assert plain.from_graph_id is None
    moved, unlanded = sorted(facts.migrations, key=lambda m: m.recorded_at)
    assert (moved.to_graph_id, moved.landed_node_id, moved.from_node_id, moved.from_graph_id, moved.epoch) == (
        "gr_2",
        "nd_9",
        "nd_2",
        "gr_1",
        4,
    )
    assert (unlanded.to_graph_id, unlanded.landed_node_id, unlanded.choice_name) == ("gr_1", None, "back")
    assert (facts.project_id is not None, facts.graph_id) == (True, "gr_1")


def test_chunk_facts_reads_mints_escalations_usage_endings_requeues_restarts_and_title(tmp_path: Path) -> None:
    store, engine = _store(tmp_path, "ch_1")
    _insert(engine, s.lease_facts, chunk_id="ch_1", epoch=1, runner_id="rn_1", minted_at=_at(1), lease_id="ls_1")
    # A forced entry's takeover: the runner's mint, under its own ownership, naming no lease.
    _insert(engine, s.epoch_owners, chunk_id="ch_1", epoch=2, runner_id="rn_1", recorded_at=_at(2))
    _insert(engine, s.lease_facts, chunk_id="ch_1", epoch=2, runner_id="rn_1", minted_at=_at(2), lease_id=None)
    # The hub's own mint: its epoch owned by the hub.
    _insert(engine, s.epoch_owners, chunk_id="ch_1", epoch=3, runner_id=None, recorded_at=_at(6))
    _insert(engine, s.lease_facts, chunk_id="ch_1", epoch=3, runner_id="hub", minted_at=_at(6), lease_id=None)
    _insert(
        engine,
        s.escalations,
        chunk_id="ch_1",
        epoch=1,
        cause="stuck",
        detail="no progress",
        recorded_at=_at(3),
    )
    _insert(
        engine,
        s.usage_facts,
        chunk_id="ch_1",
        node_id="nd_1",
        epoch=1,
        runner_id="rn_1",
        kind="judge",
        model="opus",
        harness_id="claude",
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=30,
        cache_create_tokens=40,
        cost_usd=None,
        estimated_cost_usd=0.5,
        recorded_at=_at(2),
    )
    _insert(engine, s.chunk_stopped, chunk_id="ch_1", stopped_at=_at(9), stopped_by="u")
    _insert(engine, s.chunk_completed, chunk_id="ch_1", completed_at=_at(7), completed_by="u")
    _insert(engine, s.requeues, chunk_id="ch_1", requeued_at=_at(4))
    _insert(
        engine,
        s.chunk_restarts,
        chunk_id="ch_1",
        graph_id="gr_1",
        to_node_id="nd_1",
        epoch=3,
        restarted_by="u",
        recorded_at=_at(5),
    )
    _insert(engine, s.chunk_work_refs, chunk_id="ch_1", source="hub", ref="1")
    _insert(engine, s.chunk_work_refs, chunk_id="ch_1", source="hub", ref="2")
    for ref, title in (("1", "first"), ("2", "second")):
        _insert(
            engine,
            s.work_items,
            work_item_id=f"wi_{ref}",
            source="hub",
            ref=ref,
            title=title,
            body="",
            author_kind="user",
            author_payload="{}",
            created_at=_T0,
            edited_at=_T0,
        )

    facts = store.chunk_facts(["ch_1"])["ch_1"]

    assert sorted((le.epoch, le.lease_id, le.minted_at, le.hub) for le in facts.leases) == [
        (1, "ls_1", _at(1), False),
        (2, None, _at(2), False),
        (3, None, _at(6), True),
    ]
    (esc,) = facts.escalations
    assert (esc.cause, esc.detail, esc.recorded_at, esc.epoch) == ("stuck", "no progress", _at(3), 1)
    assert (facts.requeues, facts.restarts) == ((_at(4),), (_at(5),))
    (use,) = facts.usage
    assert (use.node_id, use.kind, use.model, use.harness_id, use.epoch) == ("nd_1", "judge", "opus", "claude", 1)
    assert (use.input_tokens, use.output_tokens, use.cache_read_tokens, use.cache_create_tokens) == (10, 20, 30, 40)
    assert (use.cost_usd, use.estimated_cost_usd) == (None, 0.5)
    assert facts.endings == (_at(7), _at(9))
    assert facts.title == "first"


def test_chunk_facts_drops_an_unknown_chunk_id(tmp_path: Path) -> None:
    store, _ = _store(tmp_path, "ch_1")

    assert set(store.chunk_facts(["ch_1", "ch_missing"])) == {"ch_1"}


def test_roster_names_runners_with_their_hosts_and_hosts_by_id(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _insert(engine, s.hosts, host_id="hs_1", name="xps", added_at=_T0)
    _insert(engine, s.runner_registrations, runner_id="rn_1", name="alpha", added_at=_T0, host_id="hs_1")
    _insert(engine, s.runner_registrations, runner_id="rn_2", name="solo", added_at=_T0)

    roster = store.roster()

    assert {k: (v.name, v.host_id) for k, v in roster.runners.items()} == {
        "rn_1": ("alpha", "hs_1"),
        "rn_2": ("solo", None),
    }
    assert dict(roster.hosts) == {"hs_1": "xps"}


def test_chunk_facts_statement_count_is_flat_across_chunks(tmp_path: Path) -> None:
    ids = [f"ch_{n:02d}" for n in range(30)]
    store, engine = _store(tmp_path, *ids)

    one = count_queries(engine, lambda: store.chunk_facts(ids[:1]))
    many = count_queries(engine, lambda: store.chunk_facts(ids))

    assert one == many
