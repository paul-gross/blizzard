"""The store-read-index gate — runner and hub halves.

Drives every read method of every runner/hub ``IRead*`` Protocol against a real, migrated-to-head sqlite store and
fails if any reads a table whole — a scan, an automatic index, an ordered walk of a named index, or a search on the
tenant prefix alone — where ``tests/store_scan_allowlist.py``'s allow-lists name no reason for it, coverage enforced by reflection equality against ``tests/store_read_census.py``'s census/exemptions.
``tests/test_runner_store_indexes.py``/``tests/test_chunk_fact_table_indexes.py`` keep their own narrower purpose."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import blizzard.hub as hub_pkg
import blizzard.runner as runner_pkg
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.observability.egress.repository import IReadEgressEvents, IReadUsageCorpus
from blizzard.hub.domain.observability.tracing.repository import IReadStepCorpus
from blizzard.hub.domain.observability.transcripts import IReadTranscriptSegments
from blizzard.hub.store import schema as hub_schema
from blizzard.runner.store import schema as runner_schema
from tests import support
from tests.runner_fakes import runner_migration_prototype
from tests.store_read_census import (
    HUB_CENSUS,
    HUB_EXEMPTIONS,
    HUB_NAMED_READ_SEAMS,
    RUNNER_CENSUS,
    RUNNER_EXEMPTIONS,
    HubWorld,
    RunnerWorld,
    build_hub_world,
    build_runner_world,
)
from tests.store_scan_allowlist import (
    HUB_ALLOWED_SCANS,
    ROW_THRESHOLD,
    RUNNER_ALLOWED_SCANS,
    Allowance,
    IndexWalkAllowance,
    MethodScopedAllowance,
    TableWideAllowance,
    walks_a_named_index,
)

pytestmark = pytest.mark.component


def _reflect_read_protocol_methods(package: ModuleType) -> set[tuple[type, str]]:
    """Walk ``package`` for every ``IRead*`` ``Protocol`` class, keyed ``(ProtocolClass, method_name)``
    for each method declared **directly** in that class's own body (``vars(cls)``) — never one only inherited from a
    composed base, so an umbrella such as ``IReadRunnerStore`` contributes none of its own. Called once for
    ``blizzard.runner`` and once for ``blizzard.hub``, rather than duplicated per store."""
    keys: set[tuple[type, str]] = set()
    for modinfo in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        if modinfo.name.endswith(".migrations.env"):
            continue  # src/blizzard/*/store/migrations/env.py needs an active `alembic` CLI context
        module = importlib.import_module(modinfo.name)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if not name.startswith("IRead") or obj.__module__ != module.__name__:
                continue
            if not getattr(obj, "_is_protocol", False):
                continue
            for member_name, member in vars(obj).items():
                if member_name.startswith("_") or not callable(member):
                    continue
                keys.add((obj, member_name))
    return keys


def _declared_methods(protocols: tuple[type, ...]) -> set[tuple[type, str]]:
    return {
        (protocol, name)
        for protocol in protocols
        for name, member in vars(protocol).items()
        if not name.startswith("_") and callable(member)
    }


def _offending_scans_by_method(
    engine: sa.Engine, world: object, census: dict[tuple[type, str], Any], tables: set[str]
) -> list[tuple[type, str, str, sa.Row[Any]]]:
    """Every ``(Protocol, method, table, plan row)`` offense, driving each census recipe under its own capture so an
    offense is attributable to the read that caused it — what the allow-list's method-scoped entries and the hygiene
    check below both need. ``tables`` is the caller's own ``schema.metadata`` table-name vocabulary, passed in rather
    than imported, so this stays a plain reflection over the recipe with no store-specific import of its own."""
    offenders: list[tuple[type, str, str, sa.Row[Any]]] = []
    for (protocol, method), recipe in census.items():
        with support.capture_statements(engine) as statements:
            recipe(world)
        for table, row in support.offending_index_scans(engine, statements, tables):
            offenders.append((protocol, method, table, row))
    return offenders


def _covered(allowed: list[Allowance], protocol: type, method: str, table: str, detail: str) -> bool:
    for entry in allowed:
        if isinstance(entry, IndexWalkAllowance):
            if (
                entry.protocol is protocol
                and entry.method == method
                and table in entry.tables
                and walks_a_named_index(detail)
            ):
                return True
            continue
        if isinstance(entry, TableWideAllowance) and entry.table == table:
            return True
        if (
            isinstance(entry, MethodScopedAllowance)
            and entry.protocol is protocol
            and entry.method == method
            and entry.table == table
        ):
            return True
    return False


@pytest.fixture(scope="module")
def runner_world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunnerWorld]:
    """One migrated-to-head runner store, seeded once through its own write Protocols
    and shared read-only by every test below."""
    root = tmp_path_factory.mktemp("runner-store-gate")
    db_path = root / "runner.db"
    shutil.copyfile(runner_migration_prototype(), db_path)
    engine = create_engine_from_url(f"sqlite:///{db_path}")
    try:
        yield build_runner_world(engine)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def hub_world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[HubWorld]:
    """One migrated-to-head hub store, seeded once through its own write Protocols and
    production services, and shared read-only by every test below."""
    root = tmp_path_factory.mktemp("hub-store-gate")
    world = build_hub_world(root)
    try:
        yield world
    finally:
        world.hub.client.close()
        world.engine.dispose()


@dataclass(frozen=True)
class _StoreCase:
    """One store's own (world, schema, census, allow-list) tuple — the shape both the
    table-vocabulary and allow-list-hygiene checks below drive identically per store."""

    label: str
    world: RunnerWorld | HubWorld
    schema_module: ModuleType
    census: dict[tuple[type, str], Any]
    allowed_scans: list[Allowance]


@pytest.fixture(params=["runner", "hub"])
def store_case(request: pytest.FixtureRequest, runner_world: RunnerWorld, hub_world: HubWorld) -> _StoreCase:
    if request.param == "runner":
        return _StoreCase("runner", runner_world, runner_schema, RUNNER_CENSUS, RUNNER_ALLOWED_SCANS)
    return _StoreCase("hub", hub_world, hub_schema, HUB_CENSUS, HUB_ALLOWED_SCANS)


def test_table_vocabulary_matches_the_migrated_database(store_case: _StoreCase) -> None:
    """The gate's table vocabulary is the store's own ``schema.metadata`` table names —
    asserted equal to what the migrated database actually reflects, so the gate's own
    vocabulary and the live schema cannot silently drift apart."""
    schema_tables = set(store_case.schema_module.metadata.tables.keys())
    # Alembic's own bookkeeping table, not part of the store's own schema.metadata.
    reflected_tables = set(sa.inspect(store_case.world.engine).get_table_names()) - {"alembic_version"}
    assert schema_tables == reflected_tables


def test_allow_list_hygiene(store_case: _StoreCase) -> None:
    schema_tables = set(store_case.schema_module.metadata.tables.keys())
    offenders = _offending_scans_by_method(store_case.world.engine, store_case.world, store_case.census, schema_tables)
    offending_tables = {table for _protocol, _method, table, _row in offenders}
    offending_method_tables = {(protocol, method, table) for protocol, method, table, _row in offenders}
    walking_method_tables = {
        (protocol, method, table) for protocol, method, table, row in offenders if walks_a_named_index(row.detail)
    }

    for entry in store_case.allowed_scans:
        if isinstance(entry, IndexWalkAllowance):
            for table in entry.tables:
                assert table in schema_tables, f"{entry} names a table absent from schema.metadata: {table!r}"
                assert (entry.protocol, entry.method, table) in walking_method_tables, (
                    f"{entry} is stale — {entry.protocol.__name__}.{entry.method} no longer walks {table!r} "
                    "through a named index"
                )
            continue
        assert entry.row_bound <= ROW_THRESHOLD, f"{entry} declares a row bound above ROW_THRESHOLD={ROW_THRESHOLD}"
        assert entry.table in schema_tables, f"{entry} names a table absent from schema.metadata: {entry.table!r}"
        if isinstance(entry, TableWideAllowance):
            assert entry.table in offending_tables, (
                f"{entry} is stale — no captured {store_case.label} read reads {entry.table!r} whole any more"
            )
        else:
            assert (entry.protocol, entry.method, entry.table) in offending_method_tables, (
                f"{entry} is stale — {entry.protocol.__name__}.{entry.method} no longer reads {entry.table!r}"
            )


def test_runner_census_is_exhaustive() -> None:
    reflected = _reflect_read_protocol_methods(runner_pkg)
    declared = set(RUNNER_CENSUS.keys()) | set(RUNNER_EXEMPTIONS.keys())
    assert reflected == declared, (
        "the reflected runner IRead* Protocol methods no longer match "
        "tests/store_read_census.py's RUNNER_CENSUS | RUNNER_EXEMPTIONS — edit "
        f"tests/store_read_census.py. Missing from the census/exemptions: {reflected - declared}. "
        f"Stale in the census/exemptions (no longer reflected): {declared - reflected}."
    )


def test_every_runner_read_method_runs(runner_world: RunnerWorld) -> None:
    for (protocol, method), recipe in RUNNER_CENSUS.items():
        try:
            recipe(runner_world)
        except Exception as exc:
            raise AssertionError(f"{protocol.__name__}.{method} raised: {exc!r}") from exc


def test_runner_read_methods_never_scan_an_unallowed_table(runner_world: RunnerWorld) -> None:
    tables = set(runner_schema.metadata.tables.keys())
    offenders = _offending_scans_by_method(runner_world.engine, runner_world, RUNNER_CENSUS, tables)
    uncovered = [
        (protocol.__name__, method, table, str(row))
        for protocol, method, table, row in offenders
        if not _covered(RUNNER_ALLOWED_SCANS, protocol, method, table, row.detail)
    ]
    assert not uncovered, (
        "an offending whole-table read is not covered by tests/store_scan_allowlist.py's "
        f"RUNNER_ALLOWED_SCANS: {uncovered}"
    )


# --- hub -------------------------------------------------------------------------------


def test_hub_census_is_exhaustive() -> None:
    reflected = _reflect_read_protocol_methods(hub_pkg) | _declared_methods(HUB_NAMED_READ_SEAMS)
    declared = set(HUB_CENSUS.keys()) | set(HUB_EXEMPTIONS.keys())
    assert reflected == declared, (
        "the reflected hub IRead* Protocol methods no longer match "
        "tests/store_read_census.py's HUB_CENSUS | HUB_EXEMPTIONS — edit "
        f"tests/store_read_census.py. Missing from the census/exemptions: {reflected - declared}. "
        f"Stale in the census/exemptions (no longer reflected): {declared - reflected}."
    )


def test_every_hub_read_method_runs(hub_world: HubWorld) -> None:
    for (protocol, method), recipe in HUB_CENSUS.items():
        try:
            recipe(hub_world)
        except Exception as exc:
            raise AssertionError(f"{protocol.__name__}.{method} raised: {exc!r}") from exc


def test_hub_read_methods_never_scan_an_unallowed_table(hub_world: HubWorld) -> None:
    tables = set(hub_schema.metadata.tables.keys())
    offenders = _offending_scans_by_method(hub_world.engine, hub_world, HUB_CENSUS, tables)
    uncovered = [
        (protocol.__name__, method, table, str(row))
        for protocol, method, table, row in offenders
        if not _covered(HUB_ALLOWED_SCANS, protocol, method, table, row.detail)
    ]
    assert not uncovered, (
        "an offending whole-table read is not covered by tests/store_scan_allowlist.py's "
        f"HUB_ALLOWED_SCANS: {uncovered}"
    )


# Hub-wide time-cursor indexes kept beside a tenant-led one because a hub-scoped read pages that table across
# tenants, each with the census read that pages it. The classifier sees a lost cursor index only when the fallback
# reads the table whole; a fallback that still seeks another index's range does not, so each read is pinned to its own.
_HUB_WIDE_CURSOR_INDEXES: dict[str, tuple[str, tuple[type, str]]] = {
    "ix_transitions_recorded_at_transition_id": ("transitions", (IReadStepCorpus, "closing_candidates")),
    "ix_decisions_submitted_at_decision_id": ("decisions", (IReadStepCorpus, "closing_candidates")),
    "ix_chunk_migrations_recorded_at_migration_id": ("chunk_migrations", (IReadStepCorpus, "closing_candidates")),
    "ix_escalations_recorded_at_id": ("escalations", (IReadStepCorpus, "closing_candidates")),
    "ix_chunk_restarts_recorded_at_id": ("chunk_restarts", (IReadStepCorpus, "closing_candidates")),
    "ix_route_released_released_at_id": ("route_released", (IReadStepCorpus, "closing_candidates")),
    "ix_chunk_stopped_stopped_at_id": ("chunk_stopped", (IReadStepCorpus, "closing_candidates")),
    "ix_chunk_completed_completed_at_id": ("chunk_completed", (IReadStepCorpus, "closing_candidates")),
    "ix_epoch_owners_recorded_at_id": ("epoch_owners", (IReadStepCorpus, "closing_candidates")),
    "ix_usage_facts_recorded_at_id": ("usage_facts", (IReadUsageCorpus, "usage_after")),
    "ix_transcript_event_drops_egress_order": ("transcript_event_drops", (IReadEgressEvents, "drops_after")),
    "ix_transcript_event_derivations_egress_order": (
        "transcript_event_derivations",
        (IReadEgressEvents, "markers_after"),
    ),
    "ix_lease_facts_minted_at": ("lease_facts", (IReadEgressEvents, "epochs_minted_between")),
}


@pytest.mark.parametrize("index_name", sorted(_HUB_WIDE_CURSOR_INDEXES))
def test_each_hub_wide_cursor_index_serves_the_read_that_pages_across_tenants(
    hub_world: HubWorld, index_name: str
) -> None:
    """Dropping or renaming the index, or the read ceasing to range over it, fails here."""
    table, (protocol, method) = _HUB_WIDE_CURSOR_INDEXES[index_name]
    with support.capture_statements(hub_world.engine) as statements:
        HUB_CENSUS[(protocol, method)](hub_world)
    details = [
        row.detail
        for statement, parameters in statements
        if statement.lstrip().upper().startswith("SELECT")
        for row in support.explain_query_plan(hub_world.engine, statement, parameters)
    ]
    assert any(detail.startswith(f"SEARCH {table} USING INDEX {index_name} (") for detail in details), (
        f"{protocol.__name__}.{method} no longer searches {table} through {index_name}; its plan was {details}"
    )


# The gate's mutation self-test, one case per offense the classifier must see: a hub-scoped cursor read falling back
# to a bare scan (transitions) or to an ordered walk of another named index (decisions), and a scoped per-chunk read
# falling back to a search on the tenant prefix alone (transcript_segments).
_MUTATIONS: dict[str, tuple[str, tuple[type, str]]] = {
    "ix_transitions_recorded_at_transition_id": ("transitions", (IReadStepCorpus, "closing_candidates")),
    "ix_decisions_submitted_at_decision_id": ("decisions", (IReadStepCorpus, "closing_candidates")),
    "ix_transcript_segments_tenant_chunk_id": ("transcript_segments", (IReadTranscriptSegments, "segments_for_chunk")),
}


@pytest.mark.parametrize("index_name", sorted(_MUTATIONS))
def test_hub_mutation_dropping_an_index_the_census_exercises_is_caught_by_the_classifier(
    tmp_path_factory: pytest.TempPathFactory, index_name: str
) -> None:
    """Drops ``index_name`` on a dedicated migrated hub database and asserts ``support.offending_index_scans``
    reports its table for the read it serves — load-bearing proof that weakening any branch of
    ``support._offending_table`` (bare scan, named-index walk, tenant-prefix-only search) fails here."""
    table, (protocol, method) = _MUTATIONS[index_name]
    root = tmp_path_factory.mktemp("hub-store-gate-mutation")
    world = build_hub_world(root)
    try:
        with world.engine.begin() as conn:
            indexes_before = {row[1] for row in conn.exec_driver_sql(f"PRAGMA index_list({table})").all()}
        assert index_name in indexes_before, f"{index_name} no longer exists on {table} — pick another real hub index"

        tables = set(hub_schema.metadata.tables.keys())
        recipe = HUB_CENSUS[(protocol, method)]
        with support.capture_statements(world.engine) as statements:
            recipe(world)
        assert table not in {t for t, _row in support.offending_index_scans(world.engine, statements, tables)}, (
            f"{protocol.__name__}.{method} already reads {table} whole with {index_name} in place"
        )

        with world.engine.begin() as conn:
            conn.exec_driver_sql(f"DROP INDEX {index_name}")
        with support.capture_statements(world.engine) as statements:
            recipe(world)
        offenders = support.offending_index_scans(world.engine, statements, tables)
        assert table in {t for t, _row in offenders}, (
            f"dropping {index_name} did not surface {table} as an offending read — "
            f"{protocol.__name__}.{method}'s recipe no longer exercises that index; offenders were {offenders}"
        )
    finally:
        world.hub.client.close()
        world.engine.dispose()
