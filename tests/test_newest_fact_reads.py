"""``bzh:newest-per-key-read`` — a newest-fact read costs rows in the number of keys, not in each
key's fact history (component tier).

Each read answers the same key set at two history depths: the answer is the newest fact and
the rows fetched are identical."""

from __future__ import annotations

from collections.abc import Callable, Sized
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Column, Engine, Integer, MetaData, String, Table, create_engine, insert
from structlog import get_logger

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.config.work_sources import WorkSourceId
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreErrorFactory
from blizzard.hub.store.internal.chunk_queue_store import ChunkQueueStore
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.hub_scoped_reads import build_hub_scoped_reads
from blizzard.hub.store.internal.newest_fact import newest_fact_select, newest_retired_select
from blizzard.hub.store.internal.repository_record_store import retirements
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.trace_store import TraceStore
from blizzard.hub.store.internal.work_source_record_store import retired_ids
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantId
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from tests.config_inputs import init_host
from tests.hub_event_rows import hub_events_of
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.support import (
    HubHarness,
    build_hub,
    count_rows_read,
    default_project,
    hub_level_connections,
    hub_store_connections,
    stamp,
)

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_KEYS = ["k1", "k2", "k3"]


def _engine(tmp_path: Path, *, depth: int) -> Engine:
    """An engine holding ``depth`` facts per key in every fact table; the last is the newest."""
    tmp_path.mkdir()
    engine = build_hub(tmp_path).engine
    with engine.begin() as conn:
        for n in range(depth):
            last = n == depth - 1
            for key in _KEYS:
                conn.execute(
                    insert(s.queue_positions).values(
                        stamp(s.queue_positions, chunk_id=key, position=7.0 if last else float(n), set_at=_T0)
                    )
                )
                conn.execute(
                    insert(s.runner_pause_facts).values(
                        stamp(s.runner_pause_facts, runner_id=key, paused=last, set_at=_T0, set_by="op")
                    )
                )
                conn.execute(
                    insert(s.runner_local_pause_facts).values(
                        stamp(
                            s.runner_local_pause_facts,
                            runner_id=key,
                            paused=last,
                            set_at=_T0,
                            set_by="op",
                            reason="cause" if last else f"stale{n}",
                        )
                    )
                )
                conn.execute(
                    insert(s.runner_lifecycle_facts).values(
                        stamp(s.runner_lifecycle_facts, runner_id=key, retired=last, set_at=_T0, set_by="op")
                    )
                )
                conn.execute(
                    insert(s.graph_lifecycle_facts).values(
                        stamp(s.graph_lifecycle_facts, graph_id=key, retired=last, set_at=_T0, set_by="op")
                    )
                )
    return engine


def _reads(engine: Engine) -> dict[str, Callable[[], object]]:
    store = hub_store_connections(engine)

    def registry(fn):
        def run() -> object:
            with store.read("test") as conn:
                return fn(conn, _KEYS)

        return run

    return {
        "queue_positions": lambda: ChunkQueueStore(store, None).queue_positions(_KEYS),  # type: ignore[arg-type]
        "paused": registry(RunnerRegistryStore.paused_many),
        "local_pause": registry(RunnerRegistryStore.local_pause_detail_many),
        "lifecycle": registry(RunnerRegistryStore.lifecycle_many),
    }


def _retirement_engine(tmp_path: Path, *, depth: int) -> Engine:
    """``depth`` alternating retired/enabled facts per key in every configured-record lifecycle
    table, the last one retired for ``k1`` and ``k3`` and enabled for ``k2``."""
    tmp_path.mkdir()
    engine = build_hub(tmp_path).engine
    with engine.begin() as conn:
        for key in _KEYS:
            conn.execute(insert(s.scopes).values(stamp(s.scopes, slug=key, description="", created_at=_T0)))
        for n in range(depth):
            for key in _KEYS:
                last_retired = key != "k2"
                retired = last_retired if n == depth - 1 else not last_retired
                common = {"retired": retired, "set_at": _T0, "set_by": "op"}
                conn.execute(
                    insert(s.secret_lifecycle_facts).values(stamp(s.secret_lifecycle_facts, name=key, **common))
                )
                conn.execute(
                    insert(s.routine_lifecycle_facts).values(stamp(s.routine_lifecycle_facts, routine_id=key, **common))
                )
                conn.execute(insert(s.scope_lifecycle_facts).values(stamp(s.scope_lifecycle_facts, slug=key, **common)))
                conn.execute(
                    insert(s.repository_lifecycle_facts).values(
                        stamp(s.repository_lifecycle_facts, repository_id=key, **common)
                    )
                )
                conn.execute(
                    insert(s.work_source_lifecycle_facts).values(
                        stamp(s.work_source_lifecycle_facts, work_source_id=key, **common)
                    )
                )
                conn.execute(
                    insert(s.graph_lifecycle_facts).values(stamp(s.graph_lifecycle_facts, graph_id=key, **common))
                )
    return engine


def _retirement_reads(engine: Engine) -> dict[str, Callable[[], Sized]]:
    store = hub_store_connections(engine)
    return {
        "secret": lambda: SecretStore(store).retired_names(),
        "secret_bounded": lambda: SecretStore(store).retired_names(["k1", "k2"]),
        "routine": lambda: RoutineStore(store).retired_ids(),
        "scope": lambda: ScopeStore(store).retired_slugs(default_project(engine)),
        "graph": lambda: GraphStore(store).retired_graph_ids(),
        "repository": lambda: _read(store, lambda conn: retirements(conn)),
        "repository_bounded": lambda: _read(
            store, lambda conn: retirements(conn, [RepositoryId("k1"), RepositoryId("k2")])
        ),
        "work_source": lambda: _read(store, lambda conn: retired_ids(conn)),
        "work_source_bounded": lambda: _read(
            store, lambda conn: retired_ids(conn, [WorkSourceId("k1"), WorkSourceId("k2")])
        ),
    }


def _read(store: ScopedConnections, fn):
    with store.read("test") as conn:
        return fn(conn)


def test_retirement_reads_are_flat_across_history_depth(tmp_path: Path) -> None:
    shallow = _retirement_reads(_retirement_engine(tmp_path / "shallow", depth=1))
    deep_engine = _retirement_engine(tmp_path / "deep", depth=6)
    deep = _retirement_reads(deep_engine)
    for name in shallow:
        bounded = name.endswith("bounded")
        answer = deep[name]()
        assert answer == shallow[name](), name
        assert len(answer) == (1 if bounded else 2), name
        assert count_rows_read(deep_engine, deep[name]) <= (2 if bounded else len(_KEYS)), name


def test_newest_fact_reads_are_flat_across_history_depth(tmp_path: Path) -> None:
    shallow = _reads(_engine(tmp_path / "shallow", depth=1))
    deep_engine = _engine(tmp_path / "deep", depth=6)
    deep = _reads(deep_engine)
    for name in shallow:
        assert shallow[name]() == deep[name](), name
        assert count_rows_read(deep_engine, deep[name]) == len(_KEYS), name


def test_newest_failure_reads_keep_the_old_failure_after_a_recovery(tmp_path: Path) -> None:
    engine = build_hub(tmp_path).engine
    with engine.begin() as conn:
        for n, kind in enumerate(
            ["egress-write-failed", "egress-write-recovered", "trace-export-failed", "trace-export-recovered"]
        ):
            conn.execute(
                insert(s.hub_event_log).values(
                    recorded_at=_T0.replace(minute=n), severity="warning", kind=kind, message=f"m{n}"
                )
            )
    store = hub_level_connections(engine)
    events = hub_events_of(engine)

    corpus = build_hub_scoped_reads(store).corpus
    egress = EgressStore(store, hub_events=events, usage=corpus).newest_egress_failure()
    export = TraceStore(
        store,
        hub_events=events,
        corpus=corpus,
        hydration=None,  # type: ignore[arg-type]
        tenant_name=None,  # type: ignore[arg-type]
    ).newest_export_failure()

    assert egress is not None and egress.message == "m0"
    assert export is not None and export.message == "m2"
    assert (
        count_rows_read(engine, lambda: EgressStore(store, hub_events=events, usage=corpus).newest_egress_failure())
        <= 1
    )


def _linked_hub(tmp_path: Path, *, depth: int) -> HubHarness:
    """Projects ``p0`` and ``p1`` linked to sources ``s0`` and ``s1``, ``p1``'s link to ``s0`` cycled through
    ``depth`` unlink-and-relink rounds, then unlinked."""
    tmp_path.mkdir()
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    sources = [{"slug": s, "provider": "github", "locator": f"acme/{s}", "secret": "gh"} for s in ("s0", "s1")]
    both = [{"slug": p, "name": p, "sources": ["s0", "s1"]} for p in ("p0", "p1")]
    without = [both[0], {"slug": "p1", "sources": ["s1"]}]
    for document in [{"work_sources": sources, "projects": both}, *[{"projects": without}, {"projects": both}] * depth]:
        assert hub.client.post("/api/config/apply", json={"version": 1, **document}).status_code == 200
    assert hub.client.post("/api/config/apply", json={"version": 1, "projects": without}).status_code == 200
    return hub


def test_the_link_reads_by_source_are_flat_across_history_depth(tmp_path: Path) -> None:
    answers, rows = [], []
    for depth in (1, 6):
        hub = _linked_hub(tmp_path / str(depth), depth=depth)
        links = hub.services.project_links
        slug_of: dict[str, str] = {
            r.work_source_id: r.slug for r in hub.services.work_source_records.list_all(include_retired=False)
        }
        project_slug = {p.project_id: p.slug for p in hub.services.projects.list_all()}
        sources = sorted(slug_of)
        found = links.projects_linking_sources(sources)
        answers.append({slug_of[s]: sorted(project_slug[p] for p in projects) for s, projects in found.items()})
        rows.append(
            count_rows_read(hub.engine, lambda links=links, sources=sources: links.projects_linking_sources(sources))
        )
    assert answers[0] == answers[1] == {"s0": ["p0"], "s1": ["p0", "p1"]}
    assert rows[0] == rows[1]


# --- the newest-fact read is confined to its scope, inner group-by included -----------------------

_TEN_A = TenantId("ten_A")
_TEN_B = TenantId("ten_B")
_md = MetaData()
# A stand-in lifecycle fact table carrying the tenant key the live schema does not have yet.
_facts = Table(
    "secret_lifecycle_facts",
    _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("retired", Boolean, nullable=False),
)


def _two_tenant_facts(tmp_path: Path) -> Engine:
    """Two tenants sharing the key ``k``: A retired it then restored it; B's newer fact retires it.
    A group-by left hub-wide would pick B's id, and A's own newest fact would vanish."""
    engine = create_engine(f"sqlite:///{tmp_path / 'facts.db'}")
    _md.create_all(engine)
    with engine.begin() as conn:
        for tenant, retired in ((_TEN_A, True), (_TEN_A, False), (_TEN_B, False), (_TEN_B, True)):
            conn.execute(insert(_facts).values(tenant_id=tenant, name="k", retired=retired))
        conn.execute(insert(_facts).values(tenant_id=_TEN_B, name="b_only", retired=True))
    return engine


@pytest.mark.parametrize("keys", [None, ["k", "b_only"]], ids=["every_key", "bounded"])
def test_the_newest_fact_read_answers_only_from_its_scope_facts(tmp_path: Path, keys: list[str] | None) -> None:
    engine = _two_tenant_facts(tmp_path)
    errors = HubStoreErrorFactory(get_logger("test"))
    a = ScopedConnections(engine, errors, StoreScope(_TEN_A))
    b = ScopedConnections(engine, errors, StoreScope(_TEN_B))
    with a.read("test") as conn:
        newest_a = conn.execute(newest_fact_select(a, _facts, _facts.c.name, keys, _facts.c.name, _facts.c.retired))
        assert [tuple(r) for r in newest_a] == [("k", False)]
        assert set(conn.execute(newest_retired_select(a, _facts, _facts.c.name, keys)).scalars()) == set()
    with b.read("test") as conn:
        assert set(conn.execute(newest_retired_select(b, _facts, _facts.c.name, keys)).scalars()) == {"k", "b_only"}


# --- the runner's open takeover and open escalation per chunk, bounded by a chunk set ---------------


def _runner_history(tmp_path: Path, *, depth: int) -> tuple[SqlAlchemyRunnerStore, Engine]:
    """Chunks ``k1``..``k3``, each with ``depth`` open takeovers and ``depth`` escalated leases, epoch by epoch."""
    engine = create_engine_from_url(init_host(tmp_path).db_url)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    for key in _KEYS:
        for n in range(depth):
            at = _T0.replace(minute=n)
            store.record_takeover(
                takeover_id=f"tko_{key}_{n}",
                chunk_id=key,
                lease_id=None,
                workdir=f"/ws/{key}",
                fence_epoch=None,
                opened_at=at,
                session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{key}-{n}"),
            )
            lease_id = f"lease_{key}_{n}"
            store.record_lease(
                NewLease(
                    lease_id=lease_id,
                    chunk_id=key,
                    graph_id="gr_1",
                    node_id="nd_build",
                    node_name="build",
                    epoch=n + 1,
                    retries_max=2,
                    created_at=at,
                )
            )
            store.record_closure(lease_id=lease_id, chunk_id=key, node_id="nd_build", reason="escalated", closed_at=at)
    return store, engine


def test_the_runner_open_takeover_and_escalation_plurals_are_flat_across_history_depth(tmp_path: Path) -> None:
    shallow, _ = _runner_history(tmp_path / "shallow", depth=1)
    deep, deep_engine = _runner_history(tmp_path / "deep", depth=6)
    bounded = _KEYS[:2]
    for name in ("open_takeovers_for", "open_escalations_for"):
        answer = getattr(deep, name)(bounded)
        assert set(answer) == set(bounded), name
        assert len(getattr(shallow, name)(bounded)) == len(bounded), name
        assert count_rows_read(deep_engine, lambda name=name: getattr(deep, name)(bounded)) == len(bounded), name
    assert {k: t.takeover_id for k, t in deep.open_takeovers_for(bounded).items()} == {k: f"tko_{k}_5" for k in bounded}
    assert {k: e.epoch for k, e in deep.open_escalations_for(bounded).items()} == dict.fromkeys(bounded, 6)
