"""A runner transfer between two stores, one runner each, with one row in every table, so their surrogate keys, outbox
``seq``s, ``route_tokens`` chunk ids and ``graph_artifacts`` keys collide; only keys unique across a store's runners
(ULIDs in a real store) differ. The copy keeps every row, reference, ``seq`` and the larger counter, applies each host
table's treatment, never writes the source, and passes the invariant checker; a removal deletes one runner's rows only.
Rows are read through reflected tables, outside the runner statement guard every transfer statement runs under."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner.composition import build_host_read_stores, build_runner_removal, build_runner_transfer
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.transfer.rows import HostIngest, WorkspacePromptConflict
from blizzard.tools.invariants import runner_invariants
from tests.config_inputs import init_host
from tests.runner_fakes import FakeProbe
from tests.runner_scope_seeding import T0, reflect, seed_tables

pytestmark = pytest.mark.component

_HERE = "rn_01JX00000000000000000000AA"
_MOVING = "rn_01JX00000000000000000000BB"
_WORKSPACE = "workspace_id-1"
_RUNNER_TABLES = frozenset(t.name for t in schema.metadata.sorted_tables if t.info["scope"] == "runner")
_HOST_TABLES = frozenset(t.name for t in schema.metadata.sorted_tables if t.info["scope"] == "host")
_OUTBOX_LANES = (schema.OUTBOX_LANE_OUTBOUND, schema.OUTBOX_LANE_TRANSCRIPT)


def _errors() -> RunnerStoreErrorFactory:
    return RunnerStoreErrorFactory(get_logger("tests.runner_transfer"))


def _global_key_columns(shapes: sa.MetaData) -> frozenset[str]:
    """The string columns of every runner table's key or unique constraint that leaves out ``runner_id`` — one value
    across every runner of a store, so two real runners never share one."""
    names: set[str] = set()
    for name in _RUNNER_TABLES:
        table = shapes.tables[name]
        constraints = [table.primary_key, *(c for c in table.constraints if isinstance(c, sa.UniqueConstraint))]
        for constraint in constraints:
            columns = list(constraint.columns)
            if "runner_id" not in {c.name for c in columns}:
                names |= {c.name for c in columns if isinstance(c.type, sa.String)}
    return frozenset(names)


def _store(tmp_path: Path, name: str, runner_id: str, *, tag: str) -> sa.Engine:
    """A head store holding ``runner_id``'s identity, one row in every other runner table and every host table, and a
    counter per lane at the seeded seq; keys unique across runners are tagged ``tag``. The transcript outbox row is a
    final marker, as a finalized segment's is."""
    engine = create_engine_from_url(init_host(tmp_path / name).db_url)
    shapes = reflect(engine)
    unique = {column: f"{column}-{tag}" for column in _global_key_columns(shapes)}
    seed_tables(
        engine,
        shapes,
        _RUNNER_TABLES - {"outbox_counters"},
        values={**unique, "runner_id": runner_id, "runner_name": name, "final": True},
    )
    seed_tables(engine, shapes, _HOST_TABLES - {"host_ingests"}, values={"failure_reason": None})
    with engine.begin() as conn:
        for lane in _OUTBOX_LANES:
            conn.execute(sa.insert(shapes.tables["outbox_counters"]).values(runner_id=runner_id, lane=lane, last_seq=1))
    return engine


def _rows(engine: sa.Engine, table: str, **where: object) -> list[dict[str, Any]]:
    shape = reflect(engine).tables[table]
    stmt = sa.select(shape).where(*(shape.c[k] == v for k, v in where.items()))
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt.order_by(*shape.primary_key.columns)).mappings()]


def _everything(engine: sa.Engine) -> dict[str, list[dict[str, Any]]]:
    return {name: _rows(engine, name) for name in sorted(_RUNNER_TABLES | _HOST_TABLES)}


def _content(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """``row`` without the key the destination issues and without its references, which are judged on their own."""
    shape = schema.metadata.tables[table]
    key = shape.autoincrement_column
    return {
        name: value
        for name, value in row.items()
        if (key is None or name != key.name) and "refers_to" not in shape.c[name].info
    }


def _contents(engine: sa.Engine, table: str, **where: object) -> list[dict[str, Any]]:
    return sorted((_content(table, r) for r in _rows(engine, table, **where)), key=repr)


def _transfer(source: sa.Engine, destination: sa.Engine):
    return build_runner_transfer(source, destination, errors=_errors(), runner_id=_MOVING, clock=_Clock())


class _Clock:
    def now(self):
        return T0


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[sa.Engine, sa.Engine]]:
    here = _store(tmp_path, "here", _HERE, tag="a")
    source = _store(tmp_path, "there", _MOVING, tag="b")
    yield here, source
    here.dispose()
    source.dispose()


def test_the_colliding_stores_collide(stores: tuple[sa.Engine, sa.Engine]) -> None:
    here, source = stores
    for table in ("in_flight_elicitations", "env_bindings"):
        assert _rows(here, table)[0]["id"] == _rows(source, table)[0]["id"]
    for table in ("outbound_buffer", "transcript_outbound_buffer"):
        assert _rows(here, table)[0]["seq"] == _rows(source, table)[0]["seq"]
    assert _rows(here, "route_tokens")[0]["chunk_id"] == _rows(source, "route_tokens")[0]["chunk_id"]
    assert _rows(here, "graph_artifacts") == _rows(source, "graph_artifacts")


def test_a_copy_carries_every_row_and_reference_of_the_runner_and_leaves_the_source_as_it_was(
    stores: tuple[sa.Engine, sa.Engine],
) -> None:
    here, source = stores
    held = {name: _contents(here, name, runner_id=_HERE) for name in _RUNNER_TABLES}
    hosts = _everything(here)
    untouched = _everything(source)

    _transfer(source, here).copy_runner()

    for name in _RUNNER_TABLES:
        assert _contents(here, name, runner_id=_MOVING) == _contents(source, name, runner_id=_MOVING), name
        assert _contents(here, name, runner_id=_HERE) == held[name], name
    for table in schema.metadata.sorted_tables:
        for column in table.columns:
            target = column.info.get("refers_to")
            if target is None:
                continue
            target_table, _, target_column = target.partition(".")
            owner = {"runner_id": _MOVING} if table.info["scope"] == "runner" else {}
            for copied, original in zip(
                _rows(here, table.name, **owner)[-1:], _rows(source, table.name)[-1:], strict=True
            ):
                [referred] = _rows(here, target_table, **{target_column: copied[column.name]})
                [was] = _rows(source, target_table, **{target_column: original[column.name]})
                assert _content(target_table, referred) == _content(target_table, was), target
                if table.info["scope"] == "runner":
                    assert referred["runner_id"] == _MOVING
    [park] = _rows(here, "pause_parks", runner_id=_MOVING)
    [elicitation] = _rows(here, "in_flight_elicitations", runner_id=_MOVING)
    [their_park] = _rows(source, "pause_parks")
    assert park["interrupted_elicitation_id"] == elicitation["id"] != their_park["interrupted_elicitation_id"]
    for name in ("outbound_buffer", "transcript_outbound_buffer"):
        assert [r["seq"] for r in _rows(here, name, runner_id=_MOVING)] == [r["seq"] for r in _rows(source, name)]
    assert _rows(here, "outbox_counters", runner_id=_MOVING) == _rows(source, "outbox_counters")
    assert _rows(here, "fact_counters", runner_id=_MOVING) == _rows(source, "fact_counters")
    for name in _HOST_TABLES:
        assert _rows(here, name) == hosts[name], f"{name}: an identical host row is not carried again"
    assert _everything(source) == untouched
    assert runner_invariants(here, process=FakeProbe()).run() == []


def test_a_copy_carries_each_fact_seq_and_the_fact_counter_verbatim(stores: tuple[sa.Engine, sa.Engine]) -> None:
    here, source = stores
    tables = ("env_bindings", "binding_releases", "pause_parks", "pause_park_resumes", "requeues", "leases")
    with source.begin() as conn:
        shapes = reflect(source)
        for seq, name in enumerate(tables, start=7):
            conn.execute(sa.update(shapes.tables[name]).values(seq=seq))
        conn.execute(sa.update(shapes.tables["fact_counters"]).values(last_seq=12))

    _transfer(source, here).copy_runner()

    assert [_rows(here, name, runner_id=_MOVING)[0]["seq"] for name in tables] == list(range(7, 13))
    assert [r["last_seq"] for r in _rows(here, "fact_counters", runner_id=_MOVING)] == [12]


def test_a_copy_keeps_each_counter_at_the_larger_of_the_two_stores(stores: tuple[sa.Engine, sa.Engine]) -> None:
    here, source = stores
    with here.begin() as conn:
        shape = reflect(here).tables["outbox_counters"]
        conn.execute(sa.insert(shape).values(runner_id=_MOVING, lane=schema.OUTBOX_LANE_OUTBOUND, last_seq=9))
    with source.begin() as conn:
        shape = reflect(source).tables["outbox_counters"]
        conn.execute(sa.update(shape).values(last_seq=5))

    _transfer(source, here).copy_runner()

    counters = {r["lane"]: r["last_seq"] for r in _rows(here, "outbox_counters", runner_id=_MOVING)}
    assert counters == {schema.OUTBOX_LANE_OUTBOUND: 9, schema.OUTBOX_LANE_TRANSCRIPT: 5}


def test_a_copy_appends_a_different_history_renumbered_and_merges_graph_artifacts_by_key(
    stores: tuple[sa.Engine, sa.Engine],
) -> None:
    here, source = stores
    later = T0 + timedelta(days=1)
    with source.begin() as conn:
        shapes = reflect(source)
        for name in ("external_usage_samples", "credential_renewal_claims", "credential_renewal_outcomes"):
            shape = shapes.tables[name]
            stamps = {
                c.name: later
                for c in shape.columns
                if isinstance(schema.metadata.tables[name].c[c.name].type, UtcDateTime)
            }
            conn.execute(sa.update(shape).values(**stamps))
        # A mark is unique on its invocation, so another history's mark names an invocation of its own.
        marks, clears = shapes.tables["subscription_usage_limits"], shapes.tables["subscription_floor_clears"]
        conn.execute(sa.update(marks).values(lease_id="lease_other", marked_at=later))
        conn.execute(sa.update(clears).values(cleared_at=later))
        artifacts = shapes.tables["graph_artifacts"]
        conn.execute(
            sa.insert(artifacts).values(
                graph_id="gr_other", name="a", ordinal=0, kind="kind-1", content="c", recorded_at=T0
            )
        )
    held_claims = _rows(here, "credential_renewal_claims")

    _transfer(source, here).copy_runner()

    claims = _rows(here, "credential_renewal_claims")
    assert claims[: len(held_claims)] == held_claims
    [appended] = claims[len(held_claims) :]
    assert appended["claimed_at"] > held_claims[0]["claimed_at"] and appended["id"] != held_claims[0]["id"]
    outcomes = {r["claim_id"]: r for r in _rows(here, "credential_renewal_outcomes")}
    assert set(outcomes) == {held_claims[0]["id"], appended["id"]}
    assert outcomes[appended["id"]]["recorded_at"] > outcomes[held_claims[0]["id"]]["recorded_at"]
    assert len(_rows(here, "external_usage_samples")) == 2
    assert [r["lease_id"] for r in _rows(here, "subscription_usage_limits")] == ["lease_id-1", "lease_other"]
    assert len(_rows(here, "subscription_floor_clears")) == 2
    assert {r["graph_id"] for r in _rows(here, "graph_artifacts")} == {"graph_id-1", "gr_other"}


def test_a_copy_moves_the_runners_workspace_and_its_prompt_to_a_new_id(stores: tuple[sa.Engine, sa.Engine]) -> None:
    here, source = stores

    _transfer(source, here).copy_runner(workspace_ids={_WORKSPACE: "ws-new"})

    assert {r["workspace_id"] for r in _rows(here, "env_bindings", runner_id=_MOVING)} == {"ws-new"}
    assert {r["workspace_id"] for r in _rows(here, "env_bindings", runner_id=_HERE)} == {_WORKSPACE}
    prompts = {r["workspace_id"]: r["prompt"] for r in _rows(here, "workspace_prompt")}
    assert prompts == {_WORKSPACE: "prompt-1", "ws-new": "prompt-1"}


def test_a_different_prompt_for_one_workspace_refuses_the_copy_naming_both_and_changes_nothing(
    stores: tuple[sa.Engine, sa.Engine],
) -> None:
    here, source = stores
    with source.begin() as conn:
        conn.execute(sa.update(reflect(source).tables["workspace_prompt"]).values(prompt="theirs"))
    before = _everything(here)

    with pytest.raises(WorkspacePromptConflict, match=r"prompt-1.*theirs"):
        _transfer(source, here).copy_runner()

    assert _everything(here) == before


def test_a_copy_records_the_ingest_it_was_given(stores: tuple[sa.Engine, sa.Engine]) -> None:
    here, source = stores
    ingest = HostIngest(runner_id=_MOVING, source="/hosts/there", source_alive_at=None, ingested_at=T0)

    _transfer(source, here).copy_runner(ingest=ingest)

    read = build_host_read_stores(here, errors=_errors()).host_ingests
    assert read.latest_host_ingest(_MOVING) == ingest
    assert read.latest_host_ingest(_HERE) is None


@pytest.mark.parametrize("keep_counters", [True, False])
def test_a_removal_deletes_the_runners_rows_and_nothing_else(
    stores: tuple[sa.Engine, sa.Engine], keep_counters: bool
) -> None:
    here, source = stores
    _transfer(source, here).copy_runner()
    held = {name: _rows(here, name, runner_id=_HERE) for name in _RUNNER_TABLES}
    hosts = {name: _rows(here, name) for name in _HOST_TABLES}
    counters = _rows(here, "outbox_counters", runner_id=_MOVING)

    build_runner_removal(here, errors=_errors(), runner_id=_MOVING).remove_runner_rows(keep_counters=keep_counters)

    for name in _RUNNER_TABLES - {"outbox_counters"}:
        assert _rows(here, name, runner_id=_MOVING) == [], name  # the fact counter goes with its facts
    assert _rows(here, "outbox_counters", runner_id=_MOVING) == (counters if keep_counters else [])
    assert {name: _rows(here, name, runner_id=_HERE) for name in _RUNNER_TABLES} == held
    assert {name: _rows(here, name) for name in _HOST_TABLES} == hosts
    assert runner_invariants(here, process=FakeProbe()).run() == []


def test_a_copy_carries_the_prompt_of_a_workspace_the_runner_is_bound_to_though_no_row_of_it_names_it(
    stores: tuple[sa.Engine, sa.Engine],
) -> None:
    here, source = stores
    with source.begin() as conn:
        conn.execute(sa.delete(reflect(source).tables["env_bindings"]))

    _transfer(source, here).copy_runner(workspace_ids={_WORKSPACE: "ws-new"}, bound_workspaces=[_WORKSPACE])

    prompts = {r["workspace_id"]: r["prompt"] for r in _rows(here, "workspace_prompt")}
    assert prompts == {_WORKSPACE: "prompt-1", "ws-new": "prompt-1"}


def test_a_copy_of_a_runner_with_no_binding_and_no_row_naming_a_workspace_carries_no_prompt(
    stores: tuple[sa.Engine, sa.Engine],
) -> None:
    here, source = stores
    with source.begin() as conn:
        conn.execute(sa.delete(reflect(source).tables["env_bindings"]))
    before = _rows(here, "workspace_prompt")

    _transfer(source, here).copy_runner(workspace_ids={_WORKSPACE: "ws-new"})

    assert _rows(here, "workspace_prompt") == before


def test_a_removal_deletes_from_the_runner_tables_a_store_has_and_skips_those_it_lacks(
    stores: tuple[sa.Engine, sa.Engine],
) -> None:
    _, source = stores
    with source.begin() as conn:
        conn.execute(sa.text("DROP TABLE external_usage_samples"))

    build_runner_removal(source, errors=_errors(), runner_id=_MOVING).remove_runner_rows(keep_counters=False)

    for name in _RUNNER_TABLES - {"external_usage_samples"}:
        assert _rows(source, name, runner_id=_MOVING) == [], name
