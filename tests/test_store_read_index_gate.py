"""The store-read-index gate (blizzard#525) — runner (Phase 2) and hub (Phase 3) halves.

Drives every read method of every runner/hub ``IRead*`` Protocol against a real,
migrated-to-head sqlite store and fails if any of them plans a scan or an automatic
(covering/partial) index over a table not on ``tests/store_scan_allowlist.py``'s
``RUNNER_ALLOWED_SCANS``/``HUB_ALLOWED_SCANS``. Coverage of the read-method surface
itself is enforced by reflection equality against ``tests/store_read_census.py``'s
``RUNNER_CENSUS``/``RUNNER_EXEMPTIONS`` and ``HUB_CENSUS``/``HUB_EXEMPTIONS`` (Decision
1), not by the recipe mechanism. ``tests/test_runner_store_indexes.py`` and
``tests/test_chunk_fact_table_indexes.py`` keep their own narrower purpose — named-index
pins, plus the runner's write-path loop sweep — untouched (Decision 7)."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import blizzard.hub as hub_pkg
import blizzard.runner as runner_pkg
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunks.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.store import schema as hub_schema
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.store import schema as runner_schema
from tests import support
from tests.store_read_census import (
    HUB_CENSUS,
    HUB_EXEMPTIONS,
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
    MethodScopedAllowance,
    TableWideAllowance,
)

pytestmark = pytest.mark.component


def _reflect_read_protocol_methods(package: ModuleType) -> set[tuple[type, str]]:
    """Walk ``package`` for every ``IRead*`` ``Protocol`` class (Decision 1), keyed
    ``(ProtocolClass, method_name)`` for each method declared **directly** in that
    class's own body (``vars(cls)``) — never one only inherited from a composed base, so
    an umbrella such as the runner's ``IReadRunnerStore`` contributes none of its own,
    and a module a package walk reaches with no umbrella at all (the hub) is still
    covered. Called once for ``blizzard.runner`` and once for ``blizzard.hub``, rather
    than duplicated per store."""
    keys: set[tuple[type, str]] = set()
    for modinfo in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        try:
            module = importlib.import_module(modinfo.name)
        except Exception:
            continue
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


def _offending_scans_by_method(
    engine: sa.Engine, world: object, census: dict[tuple[type, str], Any], tables: set[str]
) -> list[tuple[type, str, str, sa.Row[Any]]]:
    """Every ``(Protocol, method, table, plan row)`` offense, driving each census recipe
    under its own capture so an offense is attributable to the read that caused it — what
    the allow-list's method-scoped entries (Decision 6) and the hygiene check below both
    need. ``tables`` is the store's own ``schema.metadata`` table-name vocabulary
    (Decision 3) — the caller's, so this stays a plain reflection over the recipe rather
    than importing either store's own ``schema`` module."""
    offenders: list[tuple[type, str, str, sa.Row[Any]]] = []
    for (protocol, method), recipe in census.items():
        with support.capture_statements(engine) as statements:
            recipe(world)
        for table, row in support.offending_index_scans(engine, statements, tables):
            offenders.append((protocol, method, table, row))
    return offenders


def _covered(
    allowed: list[TableWideAllowance | MethodScopedAllowance], protocol: type, method: str, table: str
) -> bool:
    for entry in allowed:
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
def runner_world(tmp_path_factory: pytest.TempPathFactory) -> RunnerWorld:
    """One migrated-to-head runner store, seeded once through its own write Protocols
    (Decisions 2-4) and shared read-only by every test below."""
    root = tmp_path_factory.mktemp("runner-store-gate")
    config = runner_runtime.init_environment(root)
    engine = create_engine_from_url(config.db_url)
    return build_runner_world(engine)


@pytest.fixture(scope="module")
def hub_world(tmp_path_factory: pytest.TempPathFactory) -> HubWorld:
    """One migrated-to-head hub store, seeded once through its own write Protocols and
    production services (Decisions 2-4) and shared read-only by every test below."""
    root = tmp_path_factory.mktemp("hub-store-gate")
    return build_hub_world(root)


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
        if not _covered(RUNNER_ALLOWED_SCANS, protocol, method, table)
    ]
    assert not uncovered, (
        "an offending scan/automatic-index is not covered by tests/store_scan_allowlist.py's "
        f"RUNNER_ALLOWED_SCANS: {uncovered}"
    )


def test_runner_table_vocabulary_matches_the_migrated_database(runner_world: RunnerWorld) -> None:
    """Decision 5's table vocabulary is the store's own ``schema.metadata`` table names —
    asserted equal to what the migrated database actually reflects, so the gate's own
    vocabulary and the live schema cannot silently drift apart."""
    schema_tables = set(runner_schema.metadata.tables.keys())
    # Alembic's own bookkeeping table, not part of the store's own schema.metadata.
    reflected_tables = set(sa.inspect(runner_world.engine).get_table_names()) - {"alembic_version"}
    assert schema_tables == reflected_tables


def test_runner_allow_list_hygiene(runner_world: RunnerWorld) -> None:
    schema_tables = set(runner_schema.metadata.tables.keys())
    offenders = _offending_scans_by_method(runner_world.engine, runner_world, RUNNER_CENSUS, schema_tables)
    offending_tables = {table for _protocol, _method, table, _row in offenders}
    offending_method_tables = {(protocol, method, table) for protocol, method, table, _row in offenders}

    for entry in RUNNER_ALLOWED_SCANS:
        assert entry.row_bound <= ROW_THRESHOLD, f"{entry} declares a row bound above ROW_THRESHOLD={ROW_THRESHOLD}"
        assert entry.table in schema_tables, f"{entry} names a table absent from schema.metadata: {entry.table!r}"
        if isinstance(entry, TableWideAllowance):
            assert entry.table in offending_tables, (
                f"{entry} is stale — no captured runner read scans/auto-indexes {entry.table!r} any more"
            )
        else:
            assert (entry.protocol, entry.method, entry.table) in offending_method_tables, (
                f"{entry} is stale — {entry.protocol.__name__}.{entry.method} no longer scans/auto-indexes "
                f"{entry.table!r}"
            )


# --- hub -------------------------------------------------------------------------------


def test_hub_census_is_exhaustive() -> None:
    reflected = _reflect_read_protocol_methods(hub_pkg)
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
        if not _covered(HUB_ALLOWED_SCANS, protocol, method, table)
    ]
    assert not uncovered, (
        "an offending scan/automatic-index is not covered by tests/store_scan_allowlist.py's "
        f"HUB_ALLOWED_SCANS: {uncovered}"
    )


def test_hub_table_vocabulary_matches_the_migrated_database(hub_world: HubWorld) -> None:
    """Decision 5's table vocabulary is the store's own ``schema.metadata`` table names —
    asserted equal to what the migrated database actually reflects, so the gate's own
    vocabulary and the live schema cannot silently drift apart."""
    schema_tables = set(hub_schema.metadata.tables.keys())
    # Alembic's own bookkeeping table, not part of the store's own schema.metadata.
    reflected_tables = set(sa.inspect(hub_world.engine).get_table_names()) - {"alembic_version"}
    assert schema_tables == reflected_tables


def test_hub_allow_list_hygiene(hub_world: HubWorld) -> None:
    schema_tables = set(hub_schema.metadata.tables.keys())
    offenders = _offending_scans_by_method(hub_world.engine, hub_world, HUB_CENSUS, schema_tables)
    offending_tables = {table for _protocol, _method, table, _row in offenders}
    offending_method_tables = {(protocol, method, table) for protocol, method, table, _row in offenders}

    for entry in HUB_ALLOWED_SCANS:
        assert entry.row_bound <= ROW_THRESHOLD, f"{entry} declares a row bound above ROW_THRESHOLD={ROW_THRESHOLD}"
        assert entry.table in schema_tables, f"{entry} names a table absent from schema.metadata: {entry.table!r}"
        if isinstance(entry, TableWideAllowance):
            assert entry.table in offending_tables, (
                f"{entry} is stale — no captured hub read scans/auto-indexes {entry.table!r} any more"
            )
        else:
            assert (entry.protocol, entry.method, entry.table) in offending_method_tables, (
                f"{entry} is stale — {entry.protocol.__name__}.{entry.method} no longer scans/auto-indexes "
                f"{entry.table!r}"
            )


def test_hub_mutation_dropping_an_index_the_census_exercises_is_caught_by_the_classifier(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Decision 8's mutation self-test — permanent, not manual (AC3.2). Builds its own
    dedicated migrated hub database (never ``hub_world``'s shared one), drops
    ``ix_artifacts_chunk_id_node_id_epoch`` — the index serving
    ``IReadChunkArtifactsRepository.load_artifacts``, a read :data:`HUB_CENSUS` drives —
    and asserts the classifier (``support.offending_index_scans``) reports the
    ``artifacts`` table for that read once the index is gone. Proves criterion 5's
    bare-``SCAN``/``AUTOMATIC``-index match is load-bearing: if a later change weakens
    ``support._offending_table``'s own matching, this assertion is the one that fails."""
    root = tmp_path_factory.mktemp("hub-store-gate-mutation")
    world = build_hub_world(root)
    index_name = "ix_artifacts_chunk_id_node_id_epoch"

    with world.engine.begin() as conn:
        indexes_before = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list(artifacts)").all()}
    assert index_name in indexes_before, f"{index_name} no longer exists on artifacts — pick another real hub index"

    recipe = HUB_CENSUS[(IReadChunkArtifactsRepository, "load_artifacts")]
    with world.engine.begin() as conn:
        conn.exec_driver_sql(f"DROP INDEX {index_name}")

    tables = set(hub_schema.metadata.tables.keys())
    with support.capture_statements(world.engine) as statements:
        recipe(world)
    offenders = support.offending_index_scans(world.engine, statements, tables)
    offending_tables = {table for table, _row in offenders}
    assert "artifacts" in offending_tables, (
        f"dropping {index_name} did not surface artifacts as an offending scan — "
        f"load_artifacts's own recipe no longer exercises that index; offenders were {offenders}"
    )
