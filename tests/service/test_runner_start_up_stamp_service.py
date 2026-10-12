"""Runner service tier — the start-up stamp in a real ``blizzard-runner host`` daemon.

A store whose identity row names its runner, holding rows written while it named none, is stamped when the daemon
starts: every such row carries the runner's id before the daemon serves, and so before its first ``tick end``."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.runtime import init_environment as init_runner_environment
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RUNNER_SCOPED_OPTION
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.runner_join import runner_spawn_env
from tests.service.support import poll_until, service_gate
from tests.support import daemon_log_sink, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_RUNNER_ID = "rn_01JX0000000000000000000000"
_AT = datetime(2026, 10, 1, tzinfo=UTC)
_UNSCOPED = {RUNNER_SCOPED_OPTION: True}
_SEEDED = (schema.local_pause_facts, schema.hub_control, schema.outbound_buffer)


def _seed(engine: sa.Engine) -> None:
    """The store's runner, and one row naming no runner in each of three runner-scoped tables."""
    with engine.begin() as conn:
        conn = conn.execution_options(**_UNSCOPED)
        conn.execute(
            sa.insert(schema.runner_identity), {"runner_id": _RUNNER_ID, "runner_name": "r", "registered_at": _AT}
        )
        conn.execute(sa.insert(schema.local_pause_facts), {"paused": False, "set_at": _AT, "set_by": "operator"})
        conn.execute(sa.insert(schema.hub_control), {"paused": False, "updated_at": _AT})
        conn.execute(sa.insert(schema.outbound_buffer), {"kind": "event.recorded", "payload": "{}", "created_at": _AT})


def _owners(engine: sa.Engine) -> dict[str, set[str | None]]:
    with engine.connect() as conn:
        return {
            table.name: set(conn.execute(sa.select(table.c.runner_id).execution_options(**_UNSCOPED)).scalars())
            for table in _SEEDED
        }


def test_a_runner_daemon_stamps_the_unowned_rows_before_its_first_tick(tmp_path: Path) -> None:
    runner_dir = tmp_path / "runner"
    runner_port = _free_port()
    # Never joined: the stamp is the subject, so the hub the config names is an address nothing listens on.
    unreachable_hub = f"http://127.0.0.1:{_free_port()}"
    config = init_runner_environment(runner_dir, hub_url=unreachable_hub)
    engine = create_engine_from_url(config.db_url)
    log = runner_dir / "daemon.log"
    try:
        _seed(engine)
        assert _owners(engine) == {table.name: {None} for table in _SEEDED}

        proc = subprocess.Popen(
            [
                str(Path(sys.executable).parent / "blizzard-runner"),
                "host",
                "--dir",
                str(runner_dir),
                "--host",
                "127.0.0.1",
                "--port",
                str(runner_port),
            ],
            env=runner_spawn_env(unreachable_hub),
            stdout=daemon_log_sink(log),
            stderr=subprocess.STDOUT,
            text=True,
        )
        client = httpx.Client(base_url=f"http://127.0.0.1:{runner_port}", timeout=30.0)
        try:
            _await_http(proc, client, "/api/health", log=log)
            assert _owners(engine) == {table.name: {_RUNNER_ID} for table in _SEEDED}  # stamped before serving

            assert poll_until(lambda: '"tick end"' in read_daemon_log(log), timeout=30.0)
            text = read_daemon_log(log)
            assert '"unowned runner rows stamped"' in text
            assert text.index('"unowned runner rows stamped"') < text.index('"tick end"')
            assert _owners(engine) == {table.name: {_RUNNER_ID} for table in _SEEDED}
        finally:
            client.close()
            _terminate(proc)
    finally:
        engine.dispose()
