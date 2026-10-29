"""Helpers the runner service-tier files share — a tier-neutral support module no test file owns, so a port can
delete any one of those files without orphaning another's imports."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import sqlite3
import subprocess
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx2

from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE, hub_token_variable
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import ProjectBinding, RunnerConfig, SubscriptionDeclaration
from blizzard.runner.host_config import RunnerEntry
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.leases.lease_auth import LeaseToken
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.runtime import sqlite_store_file
from blizzard.runner.store.schema import external_usage_samples
from blizzard.runner.throttle.settings import ThrottleSettings
from tests.fleet_launchers import REPO, await_http, runner_config, terminate
from tests.fleet_world import mint_world
from tests.host_files import host_config_of, init_runner_environment, write_host_file
from tests.runner_fakes import daemon_store
from tests.runner_join import RUNNER_BIN, JoinedRunner, fleet_headers, runner_spawn_env
from tests.service.support import (
    BUILD_SCRIPT,
    JUDGEMENT_SCRIPT,
    mock_hub_chunk_spec,
    poll_until,
    require_mock_fleet,
)
from tests.support import daemon_log_sink, free_port

WORK_REF_URL = f"{REPO}/issues/1"


def tick_env() -> dict[str, str]:
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    return fenced


def drive_ticks(config: RunnerConfig, fenced: dict[str, str], *, ticks: int, pause: float = 0.5) -> None:
    """Run ``ticks`` synchronous reconciliation passes with the harness fence set."""
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(ticks):
            LoopWiring.of(config).tick_once()
            time.sleep(pause)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def fleet_chunk_status(hub: httpx2.Client, chunk_id: str) -> str:
    """The mock hub's own status read."""
    return hub.get(f"/api/fleet/chunks/{chunk_id}", headers=fleet_headers(hub)).json()["status"]


def worker_credential(config: RunnerConfig, lease_id: str) -> dict[str, str]:
    """The lease-token header a worker presents, minted here rather than intercepted: the
    plaintext handed to the spawn lives only in that process's environment, and
    ``record_lease_token`` is overwrite-safe by contract — a re-mint per lease id is what
    resume and takeover already do."""
    token, token_hash = LeaseToken.mint()
    engine = create_engine_from_url(config.db_url)
    try:
        daemon_store(engine).record_lease_token(lease_id, token_hash, datetime.now(UTC))
    finally:
        engine.dispose()
    return {"X-Blizzard-Lease-Token": token}


def tick_then(config: RunnerConfig, fenced: dict[str, str], check) -> bool:
    """One tick, then evaluate ``check`` — the buffered-completion poll predicate."""
    drive_ticks(config, fenced, ticks=1, pause=0.3)
    return bool(check())


CONFIGURED_RUNNER = JoinedRunner(
    runner_id="rn_01JX00000000000000000HALTA", runner_name="runner-local", token="tok_halt_a"
)


@contextlib.contextmanager
def single_runner_host(
    root: Path, hub_url: str, token: str, *, subscription: str | None = None, store_names: str | None = None
) -> Iterator[httpx2.Client]:
    """``blizzard-runner host`` over ``root``, its one runner configured as ``CONFIGURED_RUNNER`` and holding ``token``,
    naming ``subscription`` when given and over a store whose identity row names ``store_names`` when given; yields a client to its port once it answers ``/api/health``."""
    port = free_port()
    config = init_runner_environment(root)
    write_host_file(
        dataclasses.replace(
            config,
            host="127.0.0.1",
            port=port,
            hub_url=hub_url,
            runner_id=CONFIGURED_RUNNER.runner_id,
            subscriptions=(),
        )
    )
    if subscription is not None:
        path = HostLayout(root).config_path
        named = f'name = "{config.name}"\n'
        path.write_text(path.read_text().replace(named, f'{named}subscription = "{subscription}"\n', 1))
    HubTokenFile.of(root, config.token_env).write(token)
    if store_names is not None:
        with contextlib.closing(sqlite3.connect(sqlite_store_file(config.db_url))) as conn, conn:
            conn.execute(
                "INSERT INTO runner_identity (runner_id, runner_name, registered_at) "
                "VALUES (?, 'runner-local', '2026-10-13T00:00:00+00:00')",
                (store_names,),
            )
    log = root / "daemon.log"
    proc = subprocess.Popen(
        [str(RUNNER_BIN), "host", "--dir", str(root)],
        env=runner_spawn_env(hub_url, {"BZ_RUNNER_TICK_SECONDS": "0.5"}),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
    try:
        await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        terminate(proc)


@dataclasses.dataclass(frozen=True)
class Added:
    """A runner added at the mock hub — under a host or standalone — bound in the host file to ``project``."""

    name: str
    runner_id: str
    token: str
    project: str

    @property
    def bearer(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def add_project(hub: httpx2.Client, slug: str) -> str:
    added = hub.post("/_seed/projects", json={"slug": slug, "name": slug})
    assert added.status_code == 201, added.text
    return str(added.json()["project_id"])


def add_host(hub: httpx2.Client, name: str = "host-under-test") -> tuple[str, str]:
    added = hub.post("/api/hosts", json={"name": name})
    assert added.status_code == 201, added.text
    return str(added.json()["host_id"]), str(added.json()["token"])


def add_runner(hub: httpx2.Client, name: str, project: str, *, host_id: str | None = None) -> Added:
    body: dict[str, object] = {"name": name, "projects": [project]}
    if host_id is not None:
        body["host_id"] = host_id
    added = hub.post("/api/runners", json=body)
    assert added.status_code == 201, added.text
    return Added(name=name, runner_id=str(added.json()["runner_id"]), token=str(added.json()["token"]), project=project)


def host_rows(root: Path, query: str, *params: object) -> list[tuple]:
    with contextlib.closing(sqlite3.connect(root / "data" / "host.db")) as conn:
        return conn.execute(query, params).fetchall()


def host_leased(root: Path, runner_id: str, chunk_id: str) -> bool:
    return bool(host_rows(root, "SELECT 1 FROM leases WHERE runner_id = ? AND chunk_id = ?", runner_id, chunk_id))


def asserted_chunk_status(hub: httpx2.Client, chunk_id: str) -> str:
    detail = hub.get(f"/api/fleet/chunks/{chunk_id}", headers=fleet_headers(hub))
    assert detail.status_code == 200, detail.text
    return str(detail.json()["status"])


def mock_fleet_world(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    return bin_dir, workspace


def mint_lease(config: RunnerConfig, fenced: dict[str, str], runner_client: httpx2.Client, chunk_id: str) -> str:
    """Drive ticks until the chunk's first lease mints, returning its own lease id."""

    def _lease_minted() -> bool:
        items = runner_client.get("/api/leases").json()["items"]
        return any(item["chunk_id"] == chunk_id for item in items)

    minted = poll_until(lambda: tick_then(config, fenced, _lease_minted), timeout=60.0)
    assert minted, "the chunk's first lease never minted"
    items = runner_client.get("/api/leases").json()["items"]
    return next(item["lease_id"] for item in items if item["chunk_id"] == chunk_id)


#: The window every seeded sample reports — the one the limit falls in.
USAGE_WINDOW = "five_hour"


#: Slots per runner: enough that a parked lease never starves the claims each leg asserts.
AGENTS_PER_RUNNER = 5


def limited_spec(work_ref: str, resets_at: datetime) -> dict:
    """A chunk whose build exits usage-limited at once, naming ``resets_at``."""
    spec = mock_hub_chunk_spec(work_ref)
    named = resets_at.strftime("%-I:%M%p").lower()
    spec["nodes"]["build"]["prompt"] = f'usage_limited(resets_at="{named} (UTC)")\n'
    return spec


def seed_project_chunk(hub: httpx2.Client, project_id: str, spec: dict | None = None) -> str:
    seeded = hub.post(
        "/_seed/chunk",
        json={**(spec or mock_hub_chunk_spec(f"{REPO}/issues/1")), "project_id": project_id},
    )
    assert seeded.status_code == 201, seeded.text
    return str(seeded.json()["chunk_id"])


def seed_usage_sample(root: Path, slug: str, *, utilization_pct: float, resets_at: datetime) -> None:
    """A successful sample of ``slug`` taken now, its one window at ``utilization_pct`` until ``resets_at``."""
    window = {
        "window": USAGE_WINDOW,
        "utilization_pct": utilization_pct,
        "resets_at": resets_at.isoformat(),
        "window_seconds": 5 * 3600,
    }
    engine = create_engine_from_url(f"sqlite:///{root / 'data' / 'host.db'}")
    try:
        with engine.begin() as conn:
            conn.execute(
                external_usage_samples.insert().values(
                    slug=slug, sampled_at=datetime.now(UTC), payload=json.dumps({"windows": [window]})
                )
            )
    finally:
        engine.dispose()


@contextlib.contextmanager
def two_plan_host(
    root: Path,
    bin_dir: Path,
    hub_port: int,
    workspace: Path,
    host_token: str,
    runners: tuple[tuple[Added, str], ...],
    plans: tuple[SubscriptionDeclaration, ...],
    throttle: ThrottleSettings | None = None,
    spend: dict[str, float] | None = None,
    *,
    throttles: Mapping[str, ThrottleSettings | None] | None = None,
) -> Iterator[httpx2.Client]:
    """``blizzard-runner host`` over ``root`` holding ``host_token`` and declaring ``plans``, one ``[[runner]]`` per
    ``runners`` entry spending the subscription it names, each bound to its own project at :data:`AGENTS_PER_RUNNER` slots and
    configuring ``throttle``, or what ``throttles`` names for it, with any ``spend`` fields (the ceiling and its
    window) set; yields a client to its port once it answers ``/api/health``."""
    hub_url = f"http://127.0.0.1:{hub_port}"
    base = runner_config(root, workspace, bin_dir, hub_port, join=False)
    envs = tuple(f"e{i + 1}" for i in range(len(runners) * AGENTS_PER_RUNNER))
    base = dataclasses.replace(
        base,
        subscriptions=plans,
        workspaces=(dataclasses.replace(base.workspaces[0], envs=envs, max_environments=len(envs)),),
        # Where the mock harness writes, so the limit is read from the transcript.
        transcripts_root=str(workspace / ".blizzard-mock-harness" / "transcripts"),
    )
    workspace_id = base.workspaces[0].id
    entries = tuple(
        RunnerEntry(
            name=runner.name,
            id=runner.runner_id,
            subscription=slug,
            bindings=(ProjectBinding(runner.project, workspace_id, AGENTS_PER_RUNNER),),
            own=dataclasses.replace(
                base,
                name=runner.name,
                runner_id=runner.runner_id,
                throttle=(throttles or {}).get(runner.name, throttle),
                **(spend or {}),
            ),
        )
        for runner, slug in runners
    )
    HostLayout(root).config_path.write_text(dataclasses.replace(host_config_of(base), runners=entries).to_toml())
    for runner, _slug in runners:
        HubTokenFile.of(root, hub_token_variable(runner.name)).write(runner.token)
    log = root / "daemon.log"
    proc = subprocess.Popen(
        [str(RUNNER_BIN), "host", "--dir", str(root)],
        env=runner_spawn_env(
            hub_url,
            {"BZ_RUNNER_TICK_SECONDS": "0.5", HOST_TOKEN_VARIABLE: host_token, "BLIZZARD_MOCK_HARNESS_FENCE": "1"},
        ),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx2.Client(base_url=f"http://127.0.0.1:{base.port}", timeout=10.0)
    try:
        await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        terminate(proc)


def declining_levers(hub: httpx2.Client, runner: Added) -> list[str]:
    """The levers the hub holds ``runner`` declining on, as its own read of itself serves them."""
    view = hub.get(f"/api/fleet/runners/{runner.runner_id}", headers={"Authorization": f"Bearer {runner.token}"})
    assert view.status_code == 200, view.text
    return [str(reason["lever"]) for reason in view.json()["declining"]]


#: How long the first node holds its step open, so a sample can land while it runs.
STEP_SECONDS = 8


def two_node_spec(work_ref: str) -> dict:
    """The scripted build chunk with a slow first node and a runner-executed ``review`` after it."""
    spec = mock_hub_chunk_spec(work_ref)
    build = spec["nodes"]["build"]
    build["prompt"] = f"import time\ntime.sleep({STEP_SECONDS})\n{BUILD_SCRIPT}"
    build["choices"] = [{"name": "pass", "description": "committed and green", "to": "review"}]
    spec["nodes"]["review"] = {
        "executor": "runner",
        "session": "resume",
        "judged_by": "worker",
        "prompt": "pass\n",
        "judgement_prompt": JUDGEMENT_SCRIPT,
        "choices": [{"name": "pass", "description": "ready to land", "to": "deliver"}],
        "retries_max": 1,
    }
    return spec


def node_leases(root: Path, chunk_id: str, node: str) -> int:
    query = "SELECT COUNT(*) FROM lease_context WHERE chunk_id = ? AND node_name = ?"
    return int(host_rows(root, query, chunk_id, node)[0][0])


def stays_unleased_node(root: Path, chunk_id: str, node: str, *, for_seconds: float) -> bool:
    """``node`` of ``chunk_id`` never gets a lease in ``for_seconds`` — several ticks."""
    return not poll_until(lambda: node_leases(root, chunk_id, node) > 0, timeout=for_seconds)
