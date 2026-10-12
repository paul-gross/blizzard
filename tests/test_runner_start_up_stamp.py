"""The runner's binding and its start-up stamp (component tier).

A fresh runner writes no runner-scoped row before registration and binds when registration adopts its id. A runner
whose store names its runner stamps every row that names none when it starts — ``blizzard runner host`` before it
serves, ``blizzard runner tick`` before it ticks — including rows the previous release's statements wrote, which name
no ``runner_id`` at all."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

import blizzard.runner.cli.runtime as runtime_cli
import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.composition import stamp_unowned_rows
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.tick import tick
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RUNNER_SCOPED_OPTION
from tests.runner_fakes import FakeHub, loop_graph
from tests.runner_scope_seeding import KEYED, T0, reflect, seed_every_keyed_table

pytestmark = pytest.mark.component

_PARENT = "20261009_1000_runner_lease_closures_lease_id_index"
_RUNNER_ID = "rn_01JX0000000000000000000000"
_UNSCOPED = {RUNNER_SCOPED_OPTION: True}


class _Stopped(Exception):
    """Raised in place of serving, once the host command has shown what it holds at that point."""


def _runtime(tmp_path: Path) -> RunnerConfig:
    root = tmp_path / "runner"
    config = runner_runtime.init_environment(root)
    return dataclasses.replace(config, workspace_root=str(tmp_path / "workspace"))


def _seed_identity(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(schema.runner_identity).execution_options(**_UNSCOPED),
            {"runner_id": _RUNNER_ID, "runner_name": "r", "registered_at": T0},
        )


def _owners(engine: sa.Engine) -> dict[str, set[str | None]]:
    """The runners every non-empty runner-scoped table's rows name, per table."""
    with engine.connect() as conn:
        owners = {
            name: set(
                conn.execute(
                    sa.select(schema.metadata.tables[name].c.runner_id).distinct().execution_options(**_UNSCOPED)
                ).scalars()
            )
            for name in sorted(KEYED | {"runner_identity"})
        }
    return {name: ids for name, ids in owners.items() if ids}


def _unowned(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return sum(
            conn.execute(
                sa.select(sa.func.count())
                .select_from(schema.metadata.tables[name])
                .where(schema.metadata.tables[name].c.runner_id.is_(None))
                .execution_options(**_UNSCOPED)
            ).scalar_one()
            for name in sorted(KEYED)
        )


def test_a_fresh_runner_writes_no_runner_scoped_row_before_registration_and_binds_at_adoption(
    tmp_path: Path,
) -> None:
    config = _runtime(tmp_path)
    hub = FakeHub(default_runner_id=_RUNNER_ID)
    hub.down = True
    with loop_graph(config) as graph:
        assert graph.connections.runner_id is None
        assert stamp_unowned_rows(graph) == 0  # nothing to stamp for: the store names no runner yet
        ctx = LoopWiring(config, "", "", None).context(hub, graph)
        try:
            tick(ctx)  # the hub is down: registration cannot adopt an id
            assert graph.connections.runner_id is None
            assert _owners(graph.engine) == {}

            hub.down = False
            tick(ctx)
        finally:
            ctx.usage_http_client.close()

        assert graph.connections.runner_id == _RUNNER_ID
        owners = _owners(graph.engine)
        assert "runner_identity" in owners
        assert all(ids == {_RUNNER_ID} for ids in owners.values()), owners


def test_a_bound_runners_host_stamps_every_unowned_row_before_it_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _runtime(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        _seed_identity(engine)
        seed_every_keyed_table(engine, reflect(engine), omit=frozenset({"runner_id"}))
        assert _unowned(engine) == len(KEYED)
        unowned_when_served: list[int] = []

        def build_hosted_app(*args: object, **kwargs: object) -> None:
            unowned_when_served.append(_unowned(engine))
            raise _Stopped

        monkeypatch.setattr(runtime_cli, "build_hosted_app", build_hosted_app)
        result = CliRunner().invoke(runner_group, ["host", "--dir", str(config.root)])

        assert isinstance(result.exception, _Stopped), result.output
        assert unowned_when_served == [0]
        assert _owners(engine) == {name: {_RUNNER_ID} for name in KEYED | {"runner_identity"}}
    finally:
        engine.dispose()


def test_rows_the_previous_release_wrote_are_stamped_at_the_next_tick_before_it_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _runtime(tmp_path)
    migration = runner_runtime.migration_runner(config)
    engine = create_engine_from_url(config.db_url)
    try:
        migration.downgrade(_PARENT)
        _seed_identity(engine)
        previous_release = reflect(engine)  # the parent's shapes name no runner_id
        migration.upgrade("head")
        seed_every_keyed_table(engine, previous_release)
        assert _unowned(engine) == len(KEYED)
        unowned_when_ticked: list[int] = []
        monkeypatch.setattr(loop_wiring, "tick", lambda ctx: unowned_when_ticked.append(_unowned(engine)))

        result = CliRunner().invoke(runner_group, ["tick", "--dir", str(config.root)])

        assert result.exit_code == 0, result.output
        assert unowned_when_ticked == [0]
        assert _owners(engine) == {name: {_RUNNER_ID} for name in KEYED | {"runner_identity"}}
    finally:
        engine.dispose()
