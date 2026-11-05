"""The process launchers every live-stack scenario shares — tier-neutral.

The service, crash, and winter tiers bring up the same daemons: the mock forge over the fixture's bare
origins, a hub daemon fronting it, and an in-process runner driven tick by tick against its own local API.
This module owns those launchers, the sibling ``blizzard-mock`` discovery they need, and the fixture
identity they address the forge and hub under.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Generator, Mapping
from pathlib import Path

import httpx2
import uvicorn

from blizzard.foundation.cli_spans import ENV_TRACE_SEND_CAP_MS
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.runner.app import build_hosted_app
from blizzard.runner.composition import RunnerProcess
from blizzard.runner.config import ENV_TRANSCRIPTS_ROOT, RunnerConfig
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.loop_wiring import LoopWiring
from tests.support.config_inputs import load_hub_config
from tests.support.fleet_world import REPO_NAME, basic_workspace
from tests.support.harness_sections import claude_code, opencode, sections
from tests.support.host_files import init_runner_environment, sampler_without_credentials
from tests.support.runner_join import join_runner
from tests.support.scaffolding import (
    LISTENING_FILE,
    await_listening_url,
    create_repositories,
    create_work_sources,
    daemon_log_sink,
    fixture_repositories,
    fixture_work_source,
    listening_args,
    published_port,
    read_daemon_log,
    write_mock_harness_credentials,
)

# The fixture project repo the loop drives, under the repository record's owner.
OWNER = "blizzard"
REPO = f"{OWNER}/{REPO_NAME}"

MOCK_HARNESS_FENCE_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"
# The vars every scenario's worker child needs, via the allowlist's operator-extension knob: the mock's own, and the
# worker send cap a loaded test machine raises.
MOCK_HARNESS_ENV_PASSTHROUGH = (MOCK_HARNESS_FENCE_VAR, ENV_TRANSCRIPTS_ROOT, ENV_TRACE_SEND_CAP_MS)


# --------------------------------------------------------------------------- #
# Workspace-layout discovery (the sibling blizzard-mock worktree)


#: Both fences a hub admits the test actions behind (``src/blizzard/hub/delivery/action_registry.py``).
TEST_ACTION_FENCES = {"BLIZZARD_TEST_ACTIONS": "1", "BLIZZARD_CRASH_FENCE": "1"}


def blizzard_root() -> Path:
    return Path(__file__).resolve().parents[2]


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


def mock_daemon_client(url: str) -> httpx2.Client:
    """A client for a ``blizzard-mock`` uvicorn daemon that pools no idle connection.

    uvicorn closes an idle connection once its keep-alive timeout lapses, and a pooled
    reuse racing that close fails with ``RemoteProtocolError``, so none is held idle.
    """
    return httpx2.Client(base_url=url, timeout=15.0, limits=httpx2.Limits(max_keepalive_connections=0))


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
def run_forge(bin_dir: Path, origins: Path, *, pinned_port: int | None = None) -> Generator[httpx2.Client]:
    """The mock forge over ``origins``, on a kernel-picked port; the client's ``base_url`` is its URL.

    ``pinned_port`` serves on that port instead and skips the listening file, for a forge binary older than
    ``--listening-file`` (the rehearsal's baseline release)."""
    log = origins.parent / "forge.log"
    listening = origins.parent / f"forge.{LISTENING_FILE}"
    proc = subprocess.Popen(
        [
            str(bin_dir / "blizzard-mock-forge"),
            "--repos-dir",
            str(origins),
            "--host",
            "127.0.0.1",
            *(["--port", str(pinned_port)] if pinned_port is not None else listening_args(listening, 0)),
        ],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        url = (
            f"http://127.0.0.1:{pinned_port}"
            if pinned_port is not None
            else await_listening_url(proc, listening, log=log)
        )
        client = mock_daemon_client(url)
    except BaseException:
        terminate(proc)
        raise
    try:
        await_http(proc, client, "/healthz", log=log)
        yield client
    finally:
        client.close()
        terminate(proc)


@contextlib.contextmanager
def run_hub(
    hub_dir: Path,
    forge_url: str,
    *,
    annotate: bool = False,
    annotation_interval_seconds: int | None = None,
    extra_env: Mapping[str, str] | None = None,
    egress: EgressConfig | None = None,
    rehost: bool = False,
    test_actions: bool = True,
) -> Generator[httpx2.Client]:
    """A hub daemon over ``hub_dir``; the client's ``base_url`` is its URL. A first start binds a kernel-picked
    port; a start over a directory a former hub published from pins that hub's port. ``rehost`` hosts a directory an earlier hub already holds, record and all,
    instead of initializing it; ``egress`` replaces the ``[egress]`` block. The test actions are admitted behind both
    of their fences unless ``test_actions`` is off, and with no crash point armed the crash fence never kills. Every
    inherited ``OTEL_*`` is stripped so a developer's own endpoint never receives test spans."""
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("OTEL_")},
        **(TEST_ACTION_FENCES if test_actions else {}),
        **(extra_env or {}),
    }
    if not test_actions:
        for fence in TEST_ACTION_FENCES:
            env.pop(fence, None)
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    if not rehost:
        subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    if annotation_interval_seconds is not None or egress is not None:
        # Flags read once, at `host` startup.
        config = load_hub_config(hub_dir)
        overrides: dict[str, object] = {}
        if annotation_interval_seconds is not None:
            overrides["annotation_interval_seconds"] = annotation_interval_seconds
        if egress is not None:
            overrides["egress"] = egress
        config = dataclasses.replace(config, **overrides)
        config.config_path.write_text(config.to_toml())
    log = hub_dir / "daemon.log"
    listening = hub_dir / LISTENING_FILE
    port = published_port(listening)
    proc = subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", *listening_args(listening, port)],
        env=env,
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        client = httpx2.Client(base_url=await_listening_url(proc, listening, log=log), timeout=30.0)
    except BaseException:
        terminate(proc)
        raise
    try:
        await_http(proc, client, "/api/health", log=log)
        if not rehost:
            # The one work source every scenario ingests against.
            sources = [fixture_work_source(REPO_NAME, REPO, forge_url, annotate=annotate)]
            create_work_sources(client, sources)
            create_repositories(client, fixture_repositories(sources, forge_url))
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
    runner_dir: Path,
    workspace: Path,
    bin_dir: Path,
    hub_url: str,
    api_socket: socket.socket | None = None,
    *,
    join: bool = True,
) -> RunnerConfig:
    """A migrated runner runtime pointed at the fixture workspace and the mock harness.
    ``port`` is ``api_socket``'s — a held :func:`~tests.support.scaffolding.bound_socket` that :func:`runner_api`
    serves on — or, with none, ``0`` for a runner daemon's ``--port`` to choose; never the base
    config's default, which can collide with this machine's live dogfood runner. Both mock
    harness binaries are always wired: harness selection is per-node, never this function, so a
    scenario opts in to OpenCode by naming ``opencode``. ``join`` first adds the runner at the hub
    at ``hub_url`` through ``runner init``, so the config carries the token it wrote to
    ``runner_dir/.env``; a scenario whose hub is not up yet passes ``join=False`` and joins later."""
    if join:
        join_runner(runner_dir, hub_url)
    base = init_runner_environment(runner_dir)  # scaffolds config + migrates the store
    claude_credentials, opencode_auth = write_mock_harness_credentials(runner_dir)
    return dataclasses.replace(
        base,
        host="127.0.0.1",
        port=api_socket.getsockname()[1] if api_socket is not None else 0,
        hub_url=hub_url,
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
    api_socket: socket.socket,
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
    ``process`` is one traced graph the ticks and the local API share; the local API serves on
    ``api_socket``, the held socket ``config`` took its port from.
    """
    prior = dict(os.environ)
    os.environ.update(fenced_env)  # the runner spawns the fenced mock harness in-process
    try:
        with runner_api(config, api_socket, process=process):
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
    config: RunnerConfig,
    api_socket: socket.socket,
    *,
    events: EventBroker | None = None,
    process: RunnerProcess | None = None,
) -> Generator[None]:
    """Serve the runner's local API in a thread on ``api_socket`` — the daemon the worker's verbs POST/GET to.

    Touches no store, so it runs alongside the tick without contention. ``events``
    threads a broker in, for a scenario proving the stream route too; ``process`` serves the app over a traced
    graph the scenario's ticks share."""
    app = build_hosted_app(config, events=events, process_graph=process).app
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [api_socket]}, name="runner-local-api", daemon=True)
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
