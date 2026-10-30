"""A pre-host runner directory graduates and its host resumes where it left off (service tier): a directory a pre-host
runner left — ``blizzard-runner.toml``, ``data/runner.db`` at the revision before the tightening, holding a held, a
parked and an escalated lease and an outbox backlog above the hub's mark — is converted by hand into a host directory,
``blizzard-runner migrate`` adopts and upgrades its store, and ``blizzard-runner host`` builds its one runner against
the mock hub, which then holds every backlog fact above the mark it had. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tomllib
from pathlib import Path

import httpx2
import pytest

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.runtime import migration_runner
from tests.config_inputs import init_host
from tests.fleet_launchers import await_http, terminate
from tests.mock_fleet_support import mock_hub, poll_until, require_mock_fleet, service_gate
from tests.runner_join import RUNNER_BIN, JoinedRunner, runner_spawn_env, seed_pinned_runner
from tests.runner_store_sql import select, utc_text
from tests.support import daemon_log_sink, free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_PARENT = "20261011_1000_runner_scope"
_RUNNER = JoinedRunner(runner_id="rn_01JX0000000000000000000GRAD", runner_name="runner-local", token="tok_graduating")
_PRE_HOST_TOKEN_ENV = "BZ_HUB_TOKEN"
#: The facts the hub applied before the runner stopped, and the backlog it left undelivered above them.
_ACKED = 3
_BACKLOG = 3


def _pre_host_directory(root: Path, *, hub_url: str, port: int) -> None:
    """Leave ``root`` as a pre-host runner left it: its store at ``data/runner.db`` at the revision before the
    tightening, its flat ``blizzard-runner.toml``, and its token under the unsuffixed variable."""
    config = init_host(root)
    migration_runner(config).downgrade(_PARENT)
    _seed_store(HostLayout(root).store_path)
    layout = HostLayout(root)
    layout.store_path.rename(layout.legacy_store_path)
    layout.config_path.unlink()
    layout.legacy_config_path.write_text(
        f'db_url = "sqlite:///{layout.legacy_store_path}"\n'
        'host = "127.0.0.1"\n'
        f"port = {port}\n"
        f'hub_url = "{hub_url}"\n'
        f'token_env = "{_PRE_HOST_TOKEN_ENV}"\n'
        f'name = "{_RUNNER.runner_name}"\n'
        f'runner_id = "{_RUNNER.runner_id}"\n'
        'workspace_id = "workspace-local"\n'
        'workspace_root = ""\n'
        'workspace_provider = "basic"\n'
        "max_environments = 10\n"
        'workspace_envs = ["e1"]\n'
        "max_agents = 3\n"
        'base_branch = "main"\n'
        "gates = []\n"
        'projects = ["default", "web"]\n'
        "[harness]\n"
        'autonomy = "dangerous"\n'
        "[transcripts]\n"
        "ship = false\n"
    )
    (root / ".env").write_text(f"{_PRE_HOST_TOKEN_ENV}={_RUNNER.token}\n")


def _seed_store(store: Path) -> None:
    """The identity row, a held lease bound to an environment, a lease parked on a question, an escalated lease, and
    an outbound lane whose first ``_ACKED`` facts were delivered and whose next ``_BACKLOG`` wait — written in SQL
    tied to the shapes at ``_PARENT``, before any table carries a ``runner_id``."""
    now = utc_text()
    conn = sqlite3.connect(store)
    try:
        (stored,) = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert stored == _PARENT, stored
        conn.execute(
            "INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES (?, ?, ?)",
            (_RUNNER.runner_id, _RUNNER.runner_name, now),
        )
        for lease_id, chunk_id in (
            ("lease_held", "ch_held"),
            ("lease_parked", "ch_parked"),
            ("lease_escalated", "ch_esc"),
        ):
            conn.execute(
                "INSERT INTO leases (lease_id, chunk_id, epoch, created_at) VALUES (?, ?, 1, ?)",
                (lease_id, chunk_id, now),
            )
            conn.execute(
                "INSERT INTO lease_context (lease_id, chunk_id, graph_id, node_id, node_name, retries_max, recorded_at)"
                " VALUES (?, ?, 'gr_1', 'nd_build', 'build', 1, ?)",
                (lease_id, chunk_id, now),
            )
        conn.execute(
            "INSERT INTO env_bindings (chunk_id, environment_id, workdir, bound_at) VALUES ('ch_held', 'env_1', '/w', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO park_facts (lease_id, chunk_id, question_id, parked_at)"
            " VALUES ('lease_parked', 'ch_parked', 'qn_1', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO lease_closures (lease_id, chunk_id, node_id, reason, closed_at, escalation_cause)"
            " VALUES ('lease_escalated', 'ch_esc', 'nd_build', 'escalated', ?, 'retries_exhausted')",
            (now,),
        )
        for index in range(_ACKED + _BACKLOG):
            conn.execute(
                "INSERT INTO outbound_buffer (kind, payload, created_at, acked_at) VALUES ('event.recorded', ?, ?, ?)",
                (json.dumps({"event": f"graduation-{index}"}), now, now if index < _ACKED else None),
            )
        conn.commit()
    finally:
        conn.close()


def _graduate(root: Path) -> None:
    """The graduation conversion an operator makes by hand: the flat file's keys move to their host homes, a
    ``db_url`` naming the directory's own ``data/runner.db`` is dropped, the old ``max_agents`` is split across the
    bindings at least one each, and the token moves to the runner's own variable."""
    layout = HostLayout(root)
    flat = tomllib.loads(layout.legacy_config_path.read_text())
    projects = list(flat["projects"])
    agents = [1] * len(projects)
    agents[0] += max(flat["max_agents"] - len(projects), 0)
    bindings = ", ".join(
        f'{{ project = "{project}", workspace = "{flat["workspace_id"]}", agents = {count} }}'
        for project, count in zip(projects, agents, strict=True)
    )
    kept_db_url = "" if flat["db_url"] == f"sqlite:///{layout.legacy_store_path}" else f'db_url = "{flat["db_url"]}"\n'
    layout.config_path.write_text(
        kept_db_url + f'host = "{flat["host"]}"\n'
        f"port = {flat['port']}\n"
        f'hub_url = "{flat["hub_url"]}"\n'
        "[transcripts]\n"
        f"ship = {str(flat['transcripts']['ship']).lower()}\n"
        "[[workspace]]\n"
        f'id = "{flat["workspace_id"]}"\n'
        f'root = "{flat["workspace_root"]}"\n'
        f'provider = "{flat["workspace_provider"]}"\n'
        f"envs = {json.dumps(flat['workspace_envs'])}\n"
        f"max_environments = {flat['max_environments']}\n"
        f'base_branch = "{flat["base_branch"]}"\n'
        "[[runner]]\n"
        f'id = "{flat["runner_id"]}"\n'
        f'name = "{flat["name"]}"\n'
        f"projects = [{bindings}]\n"
        f"gates = {json.dumps(flat['gates'])}\n"
        "[runner.harness]\n"
        f'autonomy = "{flat["harness"]["autonomy"]}"\n'
    )
    env = root / ".env"
    token = env.read_text().strip().removeprefix(f"{flat['token_env']}=")
    env.write_text(f"{hub_token_variable(flat['name'])}={token}\n")
    layout.legacy_config_path.unlink()


def _hub_mark(hub: httpx2.Client) -> int:
    """The runner's fact mark at the hub, read by an empty push under its token."""
    ack = hub.post("/api/fleet/events", json={"facts": []}, headers=_RUNNER.bearer)
    assert ack.status_code == 200, ack.text
    return int(ack.json()["high_water"])


def test_a_graduated_pre_host_directory_resumes_its_leases_and_delivers_its_backlog_above_the_hub_mark(
    tmp_path: Path,
) -> None:
    bin_dir = require_mock_fleet()
    hub_port, port = free_port(), free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "runner"
    with mock_hub(bin_dir, hub_port) as hub:
        assert hub.post("/_seed/projects", json={"slug": "web", "name": "Web"}).status_code == 201
        seed_pinned_runner(hub, _RUNNER)
        delivered = [
            {"seq": seq, "kind": "event.recorded", "payload": {"event": f"graduation-{seq - 1}"}}
            for seq in range(1, _ACKED + 1)
        ]
        assert hub.post("/api/fleet/events", json={"facts": delivered}, headers=_RUNNER.bearer).status_code == 200
        assert _hub_mark(hub) == _ACKED

        _pre_host_directory(root, hub_url=hub_url, port=port)
        _graduate(root)
        migrated = subprocess.run(
            [str(RUNNER_BIN), "migrate", "--dir", str(root)],
            capture_output=True,
            text=True,
            env=runner_spawn_env(hub_url),
            timeout=120,
        )
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        layout = HostLayout(root)
        assert layout.store_path.is_file() and not layout.legacy_store_path.exists()

        log = root / "daemon.log"
        proc = subprocess.Popen(
            [str(RUNNER_BIN), "host", "--dir", str(root)],
            env=runner_spawn_env(hub_url, {"BZ_RUNNER_TICK_SECONDS": "0.5"}),
            stdout=daemon_log_sink(log),
            stderr=subprocess.STDOUT,
            text=True,
        )
        local = httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
        try:
            await_http(proc, local, "/api/health", log=log)
            assert poll_until(lambda: _hub_mark(hub) >= _ACKED + _BACKLOG, timeout=30.0), read_daemon_log(log)

            runner = f"/runners/{_RUNNER.runner_id}"
            status = local.get(f"{runner}/api/runner")
            assert status.status_code == 200, status.text
            assert status.json()["runner_id"] == _RUNNER.runner_id
            leases = {lease["lease_id"]: lease for lease in local.get(f"{runner}/api/leases").json()["items"]}
            assert {"lease_held", "lease_parked"} <= set(leases), leases
            escalations = local.get(f"{runner}/api/escalations").json()["items"]
            assert [(e["lease_id"], e["cause"]) for e in escalations] == [("lease_escalated", "retries_exhausted")]
            # The held and parked leases are the runner's to resume: its ticks reap the unspawned one and resume the
            # parked one, each releasing a chunk the hub never handed out.
            assert poll_until(lambda: "reaping unspawned lease" in read_daemon_log(log)), read_daemon_log(log)
            assert poll_until(lambda: "park resumed" in read_daemon_log(log)), read_daemon_log(log)
        finally:
            local.close()
            terminate(proc)

    rows = select(layout.store_path, "outbound_buffer", ["runner_id", "seq", "acked_at"], order_by="seq")
    # The backlog kept its seqs, each above the hub's mark, under the runner — and the hub applied each of them, as
    # the mark it now holds shows; whatever the host wrote since follows them.
    assert [(row["runner_id"], row["seq"]) for row in rows][: _ACKED + _BACKLOG] == [
        (_RUNNER.runner_id, seq) for seq in range(1, _ACKED + _BACKLOG + 1)
    ]
    assert all(row["acked_at"] is not None for row in rows)
