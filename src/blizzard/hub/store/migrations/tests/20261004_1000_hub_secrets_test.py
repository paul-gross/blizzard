"""Tests proving the ``20261004_1000_hub_secrets`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]

_SECRETS_PARENT = "20261003_1400_escalation_cause"


def test_secret_tables_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``secrets``/``secret_lifecycle_facts`` — downgraded by id below the revision."""
    config = init_hub(tmp_path)
    runner = hub_runtime.migration_runner(config)
    tables = {"secrets", "secret_lifecycle_facts"}

    def _table_names() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert tables <= _table_names()

    runner.downgrade(_SECRETS_PARENT)
    assert not tables & _table_names()

    runner.upgrade("head")
    assert tables <= _table_names()
