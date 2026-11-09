"""A hosted wrapper releases only the host it constructs itself."""

import sqlite3
from pathlib import Path

import pytest

from blizzard.runner.app import build_hosted_app
from blizzard.runner.composition import build_runner_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_layout import HostLayout
from tests.support.runner_fakes import FIXTURE_RUNNER_ID, migrated_store_at


@pytest.mark.parametrize("ownership", ["owned", "host", "process_graph"])
def test_hosted_close_respects_engine_ownership(tmp_path: Path, ownership: str) -> None:
    config = RunnerConfig(runner_id=FIXTURE_RUNNER_ID, root=tmp_path, db_url=HostLayout(tmp_path).default_db_url)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    graph = None if ownership == "owned" else build_runner_process(config, runner_id=FIXTURE_RUNNER_ID)
    if graph is None:
        hosted = build_hosted_app(config)
    elif ownership == "host":
        hosted = build_hosted_app(config, host=graph.host)
    else:
        hosted = build_hosted_app(config, process_graph=graph)
    usage_client = graph.host.usage_http_client() if graph is not None else None

    try:
        try:
            with hosted.engine.connect() as connection:
                raw = connection.connection.driver_connection
                assert isinstance(raw, sqlite3.Connection)
                raw.execute("SELECT 1")
        finally:
            hosted.close()
        assert hosted.hub_http_client.is_closed
        assert all(client.is_closed for client in hosted.runner_clients)
        if graph is None:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                raw.execute("SELECT 1")
        else:
            assert usage_client is not None and not usage_client.is_closed
            assert raw.execute("SELECT 1").fetchone() == (1,)
            with graph.engine.connect() as connection:
                assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
    finally:
        if graph is not None:
            graph.close()
    if graph is not None:
        assert usage_client is not None and usage_client.is_closed
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            raw.execute("SELECT 1")
