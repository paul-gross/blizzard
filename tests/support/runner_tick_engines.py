"""Helpers for the runner tick bulk reads tests, shared with other test modules."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from tests.support.config_inputs import init_host


def migrated_engine(tmp_path: Path) -> Engine:
    config = init_host(tmp_path)
    return create_engine_from_url(config.db_url)
