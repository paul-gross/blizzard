"""Two registered hosts at one fake hub, for ``runner ingest`` and ``runner store reset --runner``: ``here`` holds its
scaffolded runner, ``there`` a second runner ``r2`` beside its own, with ``r2``'s identity recorded in its store."""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from click.testing import Result

from blizzard.cli.main import blizzard
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.composition import HostFiles, build_host_stores
from blizzard.runner.hub.host_identity import HostIdentity
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.tools.invariants import runner_invariants
from tests.support.config_inputs import load_host_config
from tests.support.runner_fakes import FakeProbe
from tests.support.runner_init_fakes import FakeInitHub, init_runner
from tests.support.runner_scope_seeding import T0, reflect, seed_tables

HUB = "http://hub.test:8421"
#: The runner ``there`` adds, and the id the fake hub mints it.
MOVING = "r2"
MOVING_ID = "rn_init3"
HERE_HOST = "hs_init1"
#: The tenant the fake hub adds every host in.
TENANT = "ten_default"
THERE_HOST = "hs_init2"


def run(hub: FakeInitHub, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, ["runner", *args])


def ok(hub: FakeInitHub, *args: str) -> Result:
    result = run(hub, *args)
    assert result.exit_code == 0, result.output
    return result


@dataclass(frozen=True)
class TwoHosts:
    here: Path
    there: Path

    def engine(self, root: Path) -> sa.Engine:
        return create_engine_from_url(load_host_config(root).db_url)


def two_hosts(tmp_path: Path, hub: FakeInitHub, *, adopt: bool = True, identified: bool = True) -> TwoHosts:
    """``here`` and ``there``, each a registered host, ``there`` holding ``r2`` too; with ``adopt``, the hub holds
    ``r2`` under ``here`` already, as `blizzard hub runner-host release` then `adopt` leave it. With ``identified``,
    ``here`` has recorded its host identity, as a host does at its first start."""
    here, there = tmp_path / "here", tmp_path / "there"
    ok(hub, "init", str(here), "--hub", HUB)
    ok(hub, "init", str(there), "--hub", HUB)
    ok(hub, "add", MOVING, "--project", "default", "--dir", str(there))
    ok(hub, "init", str(there), "--hub", HUB)
    if adopt:
        hub.admin.held[THERE_HOST].remove(MOVING_ID)
        hub.admin.held[HERE_HOST].append(MOVING_ID)
    if identified:
        engine = create_engine_from_url(load_host_config(here).db_url)
        build_host_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("tests.runner_ingest")), clock=SystemClock()
        ).host_identity.record_host_identity(HostIdentity(HERE_HOST, TENANT, T0))
        engine.dispose()
    return TwoHosts(here, there)


#: Left unseeded: the identity ``init`` recorded, the counters seeded per lane, and an elicitation ingest refuses.
_UNSEEDED = frozenset({"runner_identity", "outbox_counters", "in_flight_elicitations"})
RUNNER_TABLES = frozenset(t.name for t in schema.metadata.sorted_tables if t.info["scope"] == "runner")
HOST_TABLES = frozenset(t.name for t in schema.metadata.sorted_tables if t.info["scope"] == "host")


def seed_runner(
    engine: sa.Engine, runner_id: str, *, tag: str, last_seq: int = 1, workspace_id: str = "workspace_id-1"
) -> None:
    """One row in every runner table of ``runner_id`` but its identity and an open elicitation, and a counter per lane
    at ``last_seq``, its binding in ``workspace_id``; keys and environments unique across a store's runners are tagged ``tag``, so two stores seeded
    alike collide on every surrogate key, ``seq``, chunk id and graph-artifact key."""
    shapes = reflect(engine)
    unique = {column: f"{column}-{tag}" for column in _global_key_columns(shapes)}
    seed_tables(
        engine,
        shapes,
        RUNNER_TABLES - _UNSEEDED,
        omit=frozenset({"id"}),
        values={
            **unique,
            "runner_id": runner_id,
            "final": True,
            "interrupted_elicitation_id": None,
            "environment_id": f"env-{tag}",
            "workspace_id": workspace_id,
        },
    )
    with engine.begin() as conn:
        # A binding's targets name the runner's own binding, whatever key the store issued it.
        bindings, targets = shapes.tables["env_bindings"], shapes.tables["binding_targets"]
        own = sa.select(bindings.c.id).where(bindings.c.runner_id == runner_id).scalar_subquery()
        conn.execute(sa.update(targets).where(targets.c.runner_id == runner_id).values(binding_id=own))
        for lane in (schema.OUTBOX_LANE_OUTBOUND, schema.OUTBOX_LANE_TRANSCRIPT):
            conn.execute(
                sa.insert(shapes.tables["outbox_counters"]).values(runner_id=runner_id, lane=lane, last_seq=last_seq)
            )


def _global_key_columns(shapes: sa.MetaData) -> frozenset[str]:
    """The string columns of every runner table's key or unique constraint that leaves out ``runner_id`` — one value
    across every runner of a store, so two real runners never share one."""
    names: set[str] = set()
    for name in RUNNER_TABLES:
        table = shapes.tables[name]
        constraints = [table.primary_key, *(c for c in table.constraints if isinstance(c, sa.UniqueConstraint))]
        for constraint in constraints:
            columns = list(constraint.columns)
            if "runner_id" not in {c.name for c in columns}:
                names |= {c.name for c in columns if isinstance(c.type, sa.String)}
    return frozenset(names)


def seed_host(engine: sa.Engine) -> None:
    """One row in every host table an ingest carries or leaves but its own record."""
    seed_tables(
        engine, reflect(engine), HOST_TABLES - {"host_ingests", "host_identity"}, values={"failure_reason": None}
    )


def rows(engine: sa.Engine, table: str, **where: object) -> list[dict[str, Any]]:
    shape = reflect(engine).tables[table]
    stmt = sa.select(shape).where(*(shape.c[k] == v for k, v in where.items()))
    with engine.connect() as conn:
        found = conn.execute(stmt.order_by(*shape.primary_key.columns))
        return [dict(r) for r in found.mappings()]


def content(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """``row`` without the surrogate key a destination issues, nor its references."""
    shape = schema.metadata.tables[table]
    key = shape.autoincrement_column
    return {
        name: value
        for name, value in row.items()
        if (key is None or name != key.name) and "refers_to" not in shape.c[name].info
    }


def contents(engine: sa.Engine, table: str, **where: object) -> list[dict[str, Any]]:
    return sorted((content(table, r) for r in rows(engine, table, **where)), key=repr)


def invariant_violations(engine: sa.Engine) -> list[object]:
    # No recorded process is alive: the seeded leases name pids of this machine's own processes.
    return list(runner_invariants(engine, process=FakeProbe()).run())


@dataclass
class RecordingHostFiles:
    """The real host file operations, each recorded in ``journal`` by name — a context manager's entry and exit as
    ``name`` and ``name:exit`` — beside the crash points a test appends to the same list."""

    journal: list[str] = field(default_factory=list[str])
    _real: HostFiles = field(default_factory=HostFiles)

    @contextmanager
    def _recorded(self, name: str, inner: AbstractContextManager[Any]) -> Generator[Any, None, None]:
        self.journal.append(name)
        try:
            with inner as entered:
                yield entered
        finally:
            self.journal.append(f"{name}:exit")

    def store_file(self, db_url: str) -> Path:
        return self._real.store_file(db_url)

    def store_url(self, database: Path) -> str:
        return self._real.store_url(database)

    def copy_of(self, store: Path, beside: Path) -> AbstractContextManager[Path]:
        return self._recorded("copy_of", self._real.copy_of(store, beside))

    def copy_tree(self, src: Path, dst: Path) -> None:
        self.journal.append("copy_tree")
        self._real.copy_tree(src, dst)

    def copy_file(self, src: Path, dst: Path) -> None:
        self.journal.append("copy_file")
        self._real.copy_file(src, dst)

    def set_aside(self, store: Path, *, now: datetime) -> tuple[Path, ...]:
        self.journal.append("set_aside")
        return self._real.set_aside(store, now=now)

    def hold(
        self, root: Path, probe: Callable[[Path], str | None], refusal: Callable[[str], Exception]
    ) -> AbstractContextManager[None]:
        return self._recorded("hold", self._real.hold(root, probe, refusal))
