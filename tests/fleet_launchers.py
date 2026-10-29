"""The process launchers every live-stack scenario shares — tier-neutral.

The e2e, service, crash, and winter tiers bring up the same daemons: the mock forge over the fixture's bare
origins, a hub daemon fronting it, and an in-process runner driven tick by tick against its own local API.
This module owns those launchers, the sibling ``blizzard-mock`` discovery they need, and the fixture
identity they address the forge and hub under.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path

import httpx2
import uvicorn

from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.runner.app import build_hosted_app
from blizzard.runner.composition import RunnerProcess
from blizzard.runner.config import ENV_TRANSCRIPTS_ROOT, RunnerConfig
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.loop_wiring import LoopWiring
from tests.config_inputs import load_hub_config
from tests.e2e.fleet_traces import PLANT_DIR_VAR, FleetCollector
from tests.fleet_world import REPO_NAME, basic_workspace
from tests.harness_sections import claude_code, opencode, sections
from tests.host_files import init_runner_environment, sampler_without_credentials
from tests.runner_join import join_runner
from tests.support import (
    create_repositories,
    create_work_sources,
    daemon_log_sink,
    fixture_repositories,
    fixture_work_source,
    free_port,
    read_daemon_log,
    write_mock_harness_credentials,
)

# The fixture project repo the loop drives, under the repository record's owner.
OWNER = "blizzard"
REPO = f"{OWNER}/{REPO_NAME}"

MOCK_HARNESS_FENCE_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"
# The mock-only vars every scenario's worker child needs, via the allowlist's operator-extension knob.
MOCK_HARNESS_ENV_PASSTHROUGH = (MOCK_HARNESS_FENCE_VAR, ENV_TRANSCRIPTS_ROOT, PLANT_DIR_VAR)


# --------------------------------------------------------------------------- #
# Workspace-layout discovery (the sibling blizzard-mock worktree)


#: Both fences a hub admits the test actions behind (``src/blizzard/hub/delivery/action_registry.py``).
TEST_ACTION_FENCES = {"BLIZZARD_TEST_ACTIONS": "1", "BLIZZARD_CRASH_FENCE": "1"}


def blizzard_root() -> Path:
    return Path(__file__).resolve().parents[1]


def mock_bin_dir() -> Path | None:
    """The provisioned ``blizzard-mock`` virtualenv bin (sibling worktree), or None."""
    mock = blizzard_root().parent / "blizzard-mock"
    bin_dir = mock / ".venv" / "bin"
    if (bin_dir / "blizzard-mock-fixture").is_file() and (bin_dir / "mock-claude-code").is_file():
        return bin_dir
    return None


# --------------------------------------------------------------------------- #
# Process helpers


def git_bare(bare: Path, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", str(bare), *args], check=True, capture_output=True, text=True).stdout


def mock_daemon_client(port: int) -> httpx2.Client:
    """A client for a ``blizzard-mock`` uvicorn daemon that pools no idle connection.

    uvicorn closes an idle connection once its keep-alive timeout lapses, and a pooled
    reuse racing that close fails with ``RemoteProtocolError``, so none is held idle.
    """
    return httpx2.Client(
        base_url=f"http://127.0.0.1:{port}", timeout=15.0, limits=httpx2.Limits(max_keepalive_connections=0)
    )


def await_http(
    proc: subprocess.Popen[str], client: httpx2.Client, path: str, *, log: Path | None = None, timeout: float = 40.0
) -> None:
    """Block until ``path`` answers 200, or fail naming why.

    ``log`` is the daemon's own log file; the early-exit diagnostic reads it
    rather than a drained pipe, and always names the exit code too.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"process exited early ({proc.returncode}):\n{read_daemon_log(log)}")
        with contextlib.suppress(httpx2.HTTPError):
            if client.get(path).status_code == 200:
                return
        time.sleep(0.15)
    raise AssertionError(f"process did not answer {path} within {timeout}s")


@contextlib.contextmanager
def run_forge(bin_dir: Path, origins: Path, port: int) -> Iterator[httpx2.Client]:
    log = origins.parent / "forge.log"
    proc = subprocess.Popen(
        [str(bin_dir / "blizzard-mock-forge"), "--repos-dir", str(origins), "--host", "127.0.0.1", "--port", str(port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = mock_daemon_client(port)
    try:
        await_http(proc, client, "/healthz", log=log)
        yield client
    finally:
        client.close()
        terminate(proc)


@contextlib.contextmanager
def run_hub(
    hub_dir: Path,
    forge_port: int,
    port: int,
    *,
    annotate: bool = False,
    annotation_interval_seconds: int | None = None,
    extra_env: Mapping[str, str] | None = None,
    collector: FleetCollector | None = None,
    platform_spans: bool = False,
    settle_seconds: int = 0,
    egress: EgressConfig | None = None,
    rehost: bool = False,
    test_actions: bool = True,
) -> Iterator[httpx2.Client]:
    """A hub daemon over ``hub_dir``. ``rehost`` hosts a directory an earlier hub already holds, record and all,
    instead of initializing it; ``egress`` replaces the ``[egress]`` block. The test actions are admitted behind both
    of their fences unless ``test_actions`` is off, and with no crash point armed the crash fence never kills. Traced (exporting to ``collector``,
    sweeping every second, settling for ``settle_seconds``) when a usable collector is given — with platform spans
    on at a zero root sample ratio when ``platform_spans``, so only spans parented on a sampled context export;
    otherwise every inherited ``OTEL_*`` is stripped so a developer's own endpoint never receives e2e spans."""
    export_to = collector if collector is not None and collector.available else None
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("OTEL_")},
        # A short batch delay puts the inline platform spans in the file before the sweep's roots.
        **({"OTEL_EXPORTER_OTLP_ENDPOINT": export_to.endpoint, "OTEL_BSP_SCHEDULE_DELAY": "200"} if export_to else {}),
        **(TEST_ACTION_FENCES if test_actions else {}),
        **(extra_env or {}),
    }
    if not test_actions:
        for fence in TEST_ACTION_FENCES:
            env.pop(fence, None)
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    if not rehost:
        subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    if export_to or annotation_interval_seconds is not None or egress is not None:
        # Flags read once, at `host` startup.
        config = load_hub_config(hub_dir)
        overrides: dict[str, object] = {}
        if annotation_interval_seconds is not None:
            overrides["annotation_interval_seconds"] = annotation_interval_seconds
        if egress is not None:
            overrides["egress"] = egress
        if export_to:
            overrides["tracing"] = TracingConfig(
                sweep_seconds=1, settle_seconds=settle_seconds, platform=platform_spans, platform_sample_ratio=0.0
            )
        config = dataclasses.replace(config, **overrides)
        config.config_path.write_text(config.to_toml())
    log = hub_dir / "daemon.log"
    proc = subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        await_http(proc, client, "/api/health", log=log)
        if not rehost:
            # The one work source every scenario ingests against.
            forge_api = f"http://127.0.0.1:{forge_port}"
            sources = [fixture_work_source(REPO_NAME, REPO, forge_api, annotate=annotate)]
            create_work_sources(client, sources)
            create_repositories(client, fixture_repositories(sources, forge_api))
        yield client
    finally:
        client.close()
        terminate(proc)


def terminate(proc: subprocess.Popen[str]) -> None:
    proc.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)
    if proc.poll() is None:
        proc.kill()


# --------------------------------------------------------------------------- #
# The in-process runner


def runner_config(
    runner_dir: Path, workspace: Path, bin_dir: Path, hub_port: int, *, join: bool = True
) -> RunnerConfig:
    """A migrated runner runtime pointed at the fixture workspace and the mock harness.
    ``host``/``port`` bind to a free port rather than the base config's default, which
    can collide with this machine's live dogfood runner. Both mock
    harness binaries are always wired: harness selection is per-node, never this function, so a
    scenario opts in to OpenCode by naming ``opencode``. ``join`` first adds the runner at the hub
    on ``hub_port`` through ``runner init``, so the config carries the token it wrote to
    ``runner_dir/.env``; a scenario whose hub is not up yet passes ``join=False`` and joins later."""
    if join:
        join_runner(runner_dir, f"http://127.0.0.1:{hub_port}")
    base = init_runner_environment(runner_dir)  # scaffolds config + migrates the store
    claude_credentials, opencode_auth = write_mock_harness_credentials(runner_dir)
    return dataclasses.replace(
        base,
        host="127.0.0.1",
        port=free_port(),
        hub_url=f"http://127.0.0.1:{hub_port}",
        workspaces=(basic_workspace(workspace),),
        harness_sections=sections(
            # The mock façade rejects any ``--permission-mode``; both health probes read a fixture credential file.
            dataclasses.replace(
                claude_code(base),
                binary=str(bin_dir / "mock-claude-code"),
                permission_mode=None,
                credentials_path=claude_credentials,
            ),
            dataclasses.replace(opencode(base), binary=str(bin_dir / "mock-opencode"), auth_path=opencode_auth),
            base=base.harness_sections,
        ),
        # A path never created: the sampler soft-fails before any request is built.
        subscriptions=sampler_without_credentials(runner_dir),
        worker_env_passthrough=MOCK_HARNESS_ENV_PASSTHROUGH,
    )


def drive_until_done(
    config: RunnerConfig,
    hub: httpx2.Client,
    chunk_id: str,
    fenced_env: dict[str, str],
    *,
    timeout: float = 120.0,
    process: RunnerProcess | None = None,
) -> str:
    """Tick the reconciliation loop until the chunk is terminal; return its last status.

    Each tick is one synchronous REAP->PULL->FILL->ADVANCE pass, interleaved with short
    waits so the asynchronously spawned mock worker can commit before ADVANCE judges it.
    ``process`` is one traced graph the ticks and the local API share.
    """
    prior = dict(os.environ)
    os.environ.update(fenced_env)  # the runner spawns the fenced mock harness in-process
    try:
        with runner_api(config, process=process):
            deadline = time.monotonic() + timeout
            status = "ready"
            while time.monotonic() < deadline:
                LoopWiring.of(config).tick_once(process=process)
                detail = hub.get(f"/api/chunks/{chunk_id}")
                assert detail.status_code == 200, detail.text
                status = detail.json()["status"]
                if status in {"done", "stopped", "needs_human"}:
                    return status
                time.sleep(0.5)
            return status
    finally:
        os.environ.clear()
        os.environ.update(prior)


@contextlib.contextmanager
def runner_api(
    config: RunnerConfig, *, events: EventBroker | None = None, process: RunnerProcess | None = None
) -> Iterator[None]:
    """Serve the runner's local API in a thread — the daemon the worker's verbs POST/GET to.

    Touches no store, so it runs alongside the tick without contention. ``events``
    threads a broker in, for a scenario proving the stream route too; ``process`` serves the app over a traced
    graph the scenario's ticks share."""
    app = build_hosted_app(config, events=events, process_graph=process).app
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="runner-local-api", daemon=True)
    thread.start()
    client = httpx2.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
    try:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx2.HTTPError):
                if client.get("/api/health").status_code == 200:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("runner local API did not come up")
        yield
    finally:
        client.close()
        server.should_exit = True
        thread.join(timeout=10.0)
