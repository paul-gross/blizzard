"""Runner-store migrations: the store keys nothing on the runner's id and keeps one identity row."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.hub.identity import RunnerIdentity
from tests.migration_support_runner_store_migrations import (
    _LATER,
    _NEW,
    _rows,
)
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors

pytestmark = [pytest.mark.component]


def test_rebuilt_store_holds_no_identity_until_a_registration_records_the_hubs(tmp_path: Path) -> None:
    """A store keeps only the identity a registration answered, so a rebuilt one cannot fork a new
    one: it holds none until the hub answers the runner's token again."""
    identity = RunnerIdentity("rn_01JX0000000000000000000000", "r-claude", _NEW)
    first = runner_runtime.init_environment(tmp_path / "first")
    first_engine = create_engine_from_url(first.db_url)
    rebuilt = runner_runtime.init_environment(tmp_path / "rebuilt")
    rebuilt_engine = create_engine_from_url(rebuilt.db_url)
    try:
        store = SqlAlchemyRunnerStore(first_engine, runner_store_errors(), identity.runner_id)
        store.record_runner_identity(identity)
        renamed = RunnerIdentity(identity.runner_id, "r-claude-2", _LATER)
        store.record_runner_identity(renamed)
        assert store.runner_identity() == renamed
        assert _rows(first_engine, "SELECT count(*) FROM runner_identity") == [(1,)]

        again = SqlAlchemyRunnerStore(rebuilt_engine, runner_store_errors(), identity.runner_id)
        assert again.runner_identity() is None
        assert _rows(rebuilt_engine, "SELECT count(*) FROM runner_identity") == [(0,)]
        again.record_runner_identity(identity)
        assert again.runner_identity() == identity
    finally:
        first_engine.dispose()
        rebuilt_engine.dispose()
