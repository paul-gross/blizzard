"""Retirement keeps a runner's identity with each kind of retained settings record."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Table

from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.hub.client import TokenRefusal
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.hub.identity_service import RetiredRunnerWatch
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.schema import runner_setting_changes, runner_setting_entries, runner_settings
from blizzard.runner.store.scoped import RunnerStoreConnections
from tests.support.runner_fakes import make_store, runner_store_errors
from tests.support.runner_init_fakes import FakeTokenIdentityReader

_NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("table", "values"),
    [
        (runner_settings, {"revision": 1, "updated_at": _NOW}),
        (runner_setting_entries, {"key": "agents", "at_default": True, "revision": 1}),
        (
            runner_setting_changes,
            {"recorded_at": _NOW, "revision": 1, "op": "set", "door": "cli", "actor": "a", "diff": "[]"},
        ),
    ],
)
def test_forget_retains_only_the_identity_whose_settings_remain(
    tmp_path: Path, table: Table, values: dict[str, object]
) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    own = RunnerIdentityStore(store.connections)
    own.record_runner_identity(RunnerIdentity(store.connections.bound_runner_id(), "own", _NOW))
    with store.connections.begin() as conn:
        conn.execute(store.connections.insert(table).values(**values))
    assert own.forget_runner_identity() is False
    assert own.runner_identity() is not None

    with store.connections.begin() as conn:
        conn.execute(store.connections.delete(table))
    assert own.forget_runner_identity() is True
    assert own.runner_identity() is None


def test_another_runners_settings_do_not_prevent_forgetting(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    own = RunnerIdentityStore(store.connections)
    own.record_runner_identity(RunnerIdentity(store.connections.bound_runner_id(), "own", _NOW))
    sibling = RunnerStoreConnections(store.engine, runner_store_errors(), "rn_sibling")
    with sibling.begin() as conn:
        conn.execute(sibling.insert(runner_settings).values(revision=1, updated_at=_NOW))

    assert own.forget_runner_identity() is True
    assert own.runner_identity() is None


def test_retirement_watch_keeps_identity_with_history(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    identity = RunnerIdentityStore(store.connections)
    runner_id = store.connections.bound_runner_id()
    identity.record_runner_identity(RunnerIdentity(runner_id, "own", _NOW))
    with store.connections.begin() as conn:
        conn.execute(
            store.connections.insert(runner_setting_changes).values(
                recorded_at=_NOW, revision=1, op="set", door="cli", actor="a", diff="[]"
            )
        )
    watch = RetiredRunnerWatch(
        runner_id=runner_id,
        rows=identity,
        forgetter=identity,
        token_identity=FakeTokenIdentityReader(TokenRefusal(RunnerTokenRefusalReason.RETIRED, runner_id=runner_id)),
    )

    assert watch.forget_if_retired() is False
    assert identity.runner_identity() is not None
