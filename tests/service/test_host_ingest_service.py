"""A host that ingested a colliding runner resumes both (service tier): two host directories each hold one runner
whose store collides with the other's — the same outbound and transcript ``seq``s, the same ``route_tokens`` chunk
id, the same ``graph_artifacts`` key, and the same workspace id under another root — each with a held lease, a lease
parked on a question, and an outbound backlog above the mock hub's mark. ``blizzard-runner ingest --workspace-id``
moves one into the other's host, which then starts: both runners resume every lease, and the hub applies every
backlog fact above each runner's mark. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import dataclasses
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.host_config import HOST_TOKEN_VARIABLE, HostConfig
from blizzard.runner.hub.internal.token_file import HubTokenFile
from tests.fleet_launchers import await_http, terminate
from tests.host_files import init_runner_environment, write_host_file
from tests.runner_ingest_support import invariant_violations, rows
from tests.runner_join import RUNNER_BIN, JoinedRunner, runner_spawn_env
from tests.runner_scope_seeding import reflect
from tests.service.support import mock_hub, poll_until, require_mock_fleet, service_gate
from tests.support import daemon_log_sink, free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_MOVING = "r2"
_NEW_WORKSPACE = "ws-there"
#: The facts the hub applied before each runner stopped, and the backlog each left undelivered above them.
_ACKED = 2
_BACKLOG = 3
#: The keys both stores hold, so the ingest's copy collides on each.
_ROUTE_CHUNK = "ch_route_shared"
_GRAPH_ID = "gr_shared"
_PROMPT_FILE = "standing.md"
_PROMPT_FILE_TEXT = "the standing prompt\n"


def _added(hub: httpx.Client, path: str, body: dict[str, Any]) -> dict[str, Any]:
    added = hub.post(path, json=body)
    assert added.status_code == 201, added.text
    return dict(added.json())


def _runner(hub: httpx.Client, name: str, host_id: str) -> JoinedRunner:
    body = _added(hub, "/api/runners", {"name": name, "host_id": host_id})
    return JoinedRunner(runner_id=body["runner_id"], runner_name=name, token=body["token"])


def _host_directory(root: Path, runner: JoinedRunner, *, hub_url: str, port: int) -> None:
    """``root`` as a host of ``runner`` alone, its token in ``.env`` under the runner's own variable."""
    config = init_runner_environment(root)
    write_host_file(
        dataclasses.replace(
            config,
            name=runner.runner_name,
            runner_id=runner.runner_id,
            host="127.0.0.1",
            port=port,
            hub_url=hub_url,
            subscriptions=(),
        )
    )
    HubTokenFile.of(root, hub_token_variable(runner.runner_name)).write(runner.token)


def _relative_prompt_file(root: Path) -> None:
    """``root``'s workspace standing on a prompt file named relative to ``root``, which this host alone can resolve."""
    (root / _PROMPT_FILE).write_text(_PROMPT_FILE_TEXT)
    config = HostConfig.load(root).config_path
    config.write_text(config.read_text().replace('prompt_file = ""', f'prompt_file = "{_PROMPT_FILE}"', 1))


def _seed(root: Path, runner: JoinedRunner, tag: str) -> None:
    """``runner``'s identity, a held lease, a lease parked on a question, an outbound lane whose first ``_ACKED``
    facts were delivered and whose next ``_BACKLOG`` wait, an acked final transcript segment at ``seq`` 1, a route
    token for ``_ROUTE_CHUNK``, and a ``_GRAPH_ID`` artifact — every key but the leases' and chunks' the same in each
    store."""
    now = datetime.now(UTC)
    engine = create_engine_from_url(HostConfig.load(root).db_url)
    tables = reflect(engine).tables

    def insert(conn: sa.Connection, table: str, **values: object) -> None:
        if "runner_id" in tables[table].c:
            values.setdefault("runner_id", runner.runner_id)
        conn.execute(sa.insert(tables[table]).values(values))

    try:
        with engine.begin() as conn:
            insert(conn, "runner_identity", runner_name=runner.runner_name, registered_at=now)
            for kind in ("held", "parked"):
                lease_id, chunk_id = f"lease_{kind}_{tag}", f"ch_{kind}_{tag}"
                insert(conn, "leases", lease_id=lease_id, chunk_id=chunk_id, epoch=1, created_at=now)
                insert(
                    conn,
                    "lease_context",
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    graph_id=_GRAPH_ID,
                    node_id="nd_build",
                    node_name="build",
                    retries_max=1,
                    recorded_at=now,
                )
            insert(
                conn,
                "park_facts",
                lease_id=f"lease_parked_{tag}",
                chunk_id=f"ch_parked_{tag}",
                question_id=f"qn_{tag}",
                parked_at=now,
            )
            for seq in range(1, _ACKED + _BACKLOG + 1):
                insert(
                    conn,
                    "outbound_buffer",
                    seq=seq,
                    kind="event.recorded",
                    payload=json.dumps({"event": f"ingest-{tag}-{seq}"}),
                    created_at=now,
                    acked_at=now if seq <= _ACKED else None,
                )
            insert(
                conn,
                "transcript_outbound_buffer",
                seq=1,
                segment_id=f"seg_{tag}",
                chunk_id=f"ch_held_{tag}",
                final=True,
                payload=json.dumps({"segment_id": f"seg_{tag}"}),
                created_at=now,
                acked_at=now,
            )
            insert(conn, "outbox_counters", lane="outbound", last_seq=_ACKED + _BACKLOG)
            insert(conn, "outbox_counters", lane="transcript", last_seq=1)
            insert(conn, "route_tokens", chunk_id=_ROUTE_CHUNK, token=f"route-{tag}", acquired_at=now)
            insert(
                conn,
                "graph_artifacts",
                graph_id=_GRAPH_ID,
                name="spec",
                ordinal=0,
                kind="text",
                content="the same immutable mint in both stores",
                recorded_at=now,
            )
    finally:
        engine.dispose()


def _hub_mark(hub: httpx.Client, runner: JoinedRunner) -> int:
    """``runner``'s fact mark at the hub, read by an empty push under its token."""
    ack = hub.post("/api/fleet/events", json={"facts": []}, headers=runner.bearer)
    assert ack.status_code == 200, ack.text
    return int(ack.json()["high_water"])


def _deliver_acked(hub: httpx.Client, runner: JoinedRunner, tag: str) -> None:
    delivered = [
        {"seq": seq, "kind": "event.recorded", "payload": {"event": f"ingest-{tag}-{seq}"}}
        for seq in range(1, _ACKED + 1)
    ]
    assert hub.post("/api/fleet/events", json={"facts": delivered}, headers=runner.bearer).status_code == 200
    assert _hub_mark(hub, runner) == _ACKED


def _logged(log: Path, event: str, lease_id: str) -> bool:
    return any(event in line and lease_id in line for line in read_daemon_log(log).splitlines())


def test_a_host_that_ingested_a_colliding_runner_resumes_both_and_delivers_each_backlog_above_its_mark(
    tmp_path: Path,
) -> None:
    bin_dir = require_mock_fleet()
    hub_port, port = free_port(), free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    here, there = tmp_path / "here", tmp_path / "there"
    with mock_hub(bin_dir, hub_port) as hub:
        host = _added(hub, "/api/hosts", {"name": "box"})
        staying = _runner(hub, "runner-local", host["host_id"])
        moving = _runner(hub, _MOVING, host["host_id"])
        for runner, tag in ((staying, "a"), (moving, "b")):
            _deliver_acked(hub, runner, tag)
        _host_directory(here, staying, hub_url=hub_url, port=port)
        _host_directory(there, moving, hub_url=hub_url, port=free_port())
        _relative_prompt_file(there)
        HubTokenFile.of(here, HOST_TOKEN_VARIABLE).write(host["token"])
        _seed(here, staying, "a")
        _seed(there, moving, "b")

        ingested = subprocess.run(
            [
                str(RUNNER_BIN),
                "ingest",
                str(there),
                "--runner",
                _MOVING,
                "--workspace-id",
                _NEW_WORKSPACE,
                "--dir",
                str(here),
            ],
            capture_output=True,
            text=True,
            env=runner_spawn_env(hub_url),
            timeout=120,
        )
        assert ingested.returncode == 0, ingested.stdout + ingested.stderr
        carried = next(w for w in HostConfig.load(here).workspaces if w.id == _NEW_WORKSPACE)
        assert carried.prompt_file == str(HostConfig.load(there).root / _PROMPT_FILE)
        assert carried.resolved_prompt(HostConfig.load(here).root) == _PROMPT_FILE_TEXT
        engine = create_engine_from_url(HostConfig.load(here).db_url)
        try:
            for table in ("outbound_buffer", "transcript_outbound_buffer"):
                held = {(r["runner_id"], r["seq"]) for r in rows(engine, table)}
                assert {(staying.runner_id, 1), (moving.runner_id, 1)} <= held, (table, held)
            assert {r["runner_id"] for r in rows(engine, "route_tokens", chunk_id=_ROUTE_CHUNK)} == {
                staying.runner_id,
                moving.runner_id,
            }
            assert len(rows(engine, "graph_artifacts", graph_id=_GRAPH_ID)) == 1
            assert invariant_violations(engine) == []
        finally:
            engine.dispose()

        log = here / "daemon.log"
        proc = subprocess.Popen(
            [str(RUNNER_BIN), "host", "--dir", str(here)],
            env=runner_spawn_env(hub_url, {"BZ_RUNNER_TICK_SECONDS": "0.5"}),
            stdout=daemon_log_sink(log),
            stderr=subprocess.STDOUT,
            text=True,
        )
        local = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
        try:
            await_http(proc, local, "/api/health", log=log)
            for runner, tag in ((staying, "a"), (moving, "b")):
                assert poll_until(lambda r=runner: _hub_mark(hub, r) >= _ACKED + _BACKLOG, timeout=30.0), (
                    runner.runner_name,
                    read_daemon_log(log),
                )
                prefix = f"/runners/{runner.runner_id}"
                status = local.get(f"{prefix}/api/runner")
                assert status.status_code == 200, status.text
                assert status.json()["runner_id"] == runner.runner_id
                leases = {lease["lease_id"] for lease in local.get(f"{prefix}/api/leases").json()["items"]}
                assert {f"lease_held_{tag}", f"lease_parked_{tag}"} <= leases, leases
                # Each runner's ticks reap its unspawned lease and resume its parked one.
                assert poll_until(lambda t=tag: _logged(log, "reaping unspawned lease", f"lease_held_{t}")), (
                    read_daemon_log(log)
                )
                assert poll_until(lambda t=tag: _logged(log, "park resumed", f"lease_parked_{t}")), read_daemon_log(log)
        finally:
            local.close()
            terminate(proc)
