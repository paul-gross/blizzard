"""Service-tier mock-fleet launchers and gate for out-of-process daemon and CLI tests.
The runner uses the mock hub; the hub uses the mock runner and forge. External-tool tests
use the emitted CLI surface (``bzh:external-cli-fake-is-service-tier``). Requires
``BLIZZARD_SERVICE=1`` and a provisioned sibling ``blizzard-mock`` worktree."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from blizzard.hub.config import AuthConfig, OAuthProviderConfig
from blizzard.hub.graphs import PACKAGED
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from tests.config_inputs import load_hub_config
from tests.fleet_launchers import REPO, await_http, mock_bin_dir, mock_daemon_client, run_forge, run_hub, terminate
from tests.fleet_world import REPO_NAME, mint_world, scripted_build_prompt
from tests.runner_join import add_runner, runner_spawn_env
from tests.support import daemon_log_sink, free_port, shared_daemon_log_dir

# The service tier's own gate — independent of BLIZZARD_E2E so a run can select one tier.
SERVICE_ENABLED = os.environ.get("BLIZZARD_SERVICE") == "1"

service_gate = pytest.mark.skipif(
    not SERVICE_ENABLED,
    reason=(
        "service tier needs the mock fleet — a daemon's HTTP API or a mock-fleet CLI-surface "
        "binary; set BLIZZARD_SERVICE=1 (see tests/service/support.py)"
    ),
)


def require_mock_fleet() -> Path:
    """The provisioned sibling ``blizzard-mock`` venv bin with the fleet binaries, or skip."""
    bin_dir = mock_bin_dir()
    if (
        bin_dir is None
        or not (bin_dir / "blizzard-mock-hub").is_file()
        or not (bin_dir / "blizzard-mock-runner").is_file()
    ):
        pytest.skip(
            "no provisioned sibling blizzard-mock worktree with the mock hub/runner (run `winter provision <env>`)"
        )
    return bin_dir


def require_opencode_cli_surface(bin_dir: Path) -> Path:
    """``bin_dir``'s ``mock-opencode`` binary, with the ``emit`` verb live, or skip — an
    older-provisioned sibling ``blizzard-mock`` worktree may carry ``mock-opencode`` without it.
    Probed with a real ``--out`` emit rather than ``emit --help``: argparse's own ``--help``
    handling exits 0 either way, so only an actual emitted artifact tells the two apart."""
    mock_opencode = bin_dir / "mock-opencode"
    if not mock_opencode.is_file():
        pytest.skip("no provisioned sibling blizzard-mock worktree with mock-opencode (run `winter provision <env>`)")
    with tempfile.TemporaryDirectory() as probe_dir:
        probe_out = Path(probe_dir) / "probe-opencode"
        probe = subprocess.run([str(mock_opencode), "emit", "--out", str(probe_out)], capture_output=True, text=True)
        if probe.returncode != 0 or not probe_out.is_file():
            pytest.skip(
                "the provisioned sibling blizzard-mock's mock-opencode predates the emit verb "
                "(run `winter provision <env>` again)"
            )
    return mock_opencode


def require_codex_app_server_surface(bin_dir: Path) -> Path:
    """``bin_dir``'s ``mock-codex`` binary, with the ``app-server`` verb live, or skip — an
    older-provisioned sibling ``blizzard-mock`` worktree may carry ``mock-codex`` without
    it. Probed with a real ``initialize`` exchange rather than ``app-server --help``:
    argparse's own ``--help`` handling exits 0 either way, so only a real id-matched
    reply tells the two apart."""
    mock_codex = bin_dir / "mock-codex"
    if not mock_codex.is_file():
        pytest.skip("no provisioned sibling blizzard-mock worktree with mock-codex (run `winter provision <env>`)")
    with tempfile.TemporaryDirectory() as probe_dir:
        probe = subprocess.run(
            [str(mock_codex), "app-server"],
            input='{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}\n',
            capture_output=True,
            text=True,
            env={**os.environ, "CODEX_HOME": probe_dir},
            timeout=5.0,
        )
        if probe.returncode != 0 or '"id": 1' not in probe.stdout:
            pytest.skip(
                "the provisioned sibling blizzard-mock's mock-codex predates the app-server verb "
                "(run `winter provision <env>` again)"
            )
    return mock_codex


def _mock_daemon_log(name: str, port: int, log_dir: Path | None) -> Path:
    """Where one mock daemon's merged output goes (``bzh:daemon-stdout-to-file``).

    The mock fleet's daemons own no runtime directory, so ``log_dir`` defaults to
    :func:`~tests.support.shared_daemon_log_dir`; named by daemon and port so several
    concurrent instances of the same mock never share a file."""
    return (log_dir or shared_daemon_log_dir()) / f"{name}-{port}.log"


# The scripted build node: commits a file, pushes the branch, and declares it via
# `blizzard runner artifact commit`.
BUILD_SCRIPT = scripted_build_prompt(
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed by the mock harness\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    'subprocess.run(["git", "-C", repo, "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",'
    ' "commit", "-m", "feat: land a change from the mock harness"], check=True)\n'
    "_branch = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    "_commit = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    'subprocess.run(["git", "-C", repo, "push", "origin", _branch], check=True)\n'
    "subprocess.run(\n"
    '    ["blizzard", "runner", "artifact", "commit",\n'
    '     "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
    "    check=True,\n"
    ")\n"
)
JUDGEMENT_SCRIPT = "verdict('pass', 'the mock harness committed the change; checks are green')\n"

# The OpenCode-safe build node: like `BUILD_SCRIPT`, but every `subprocess.run` captures its own output — mock-opencode reads the first stdout line as the fresh-session identity.
OPENCODE_BUILD_SCRIPT = scripted_build_prompt(
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed by the mock harness\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)\n'
    'subprocess.run(["git", "-C", repo, "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",'
    ' "commit", "-m", "feat: land a change from the mock harness"], check=True, capture_output=True)\n'
    "_branch = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    "_commit = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    'subprocess.run(["git", "-C", repo, "push", "origin", _branch], check=True, capture_output=True)\n'
    "subprocess.run(\n"
    '    ["blizzard", "runner", "artifact", "commit",\n'
    '     "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
    "    check=True, capture_output=True,\n"
    ")\n"
)


def transcript_segment_turn(index: int, kind: str, text: str) -> dict:
    """One ``TurnSegmentView`` as the wire carries it, at its defaults."""
    return {
        "index": index,
        "kind": kind,
        "timestamp": None,
        "text": text,
        "tool": None,
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }


def transcript_segment_record(
    chunk_id: str,
    *,
    seq: int,
    node_id: str = "nd_build",
    epoch: int = 1,
    turns: list[dict] | None = None,
) -> dict:
    """One final ``TranscriptSegmentRecord`` on ``(chunk_id, node_id, epoch)``'s lane —
    a single ``asst`` turn unless ``turns`` says otherwise."""
    carried = turns if turns is not None else [transcript_segment_turn(0, "asst", "hi")]
    return {
        "seq": seq,
        "segment_id": "sg_1",
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": epoch,
        "spawn_generation": 1,
        "turn_range_start": carried[0]["index"],
        "turn_range_end": carried[-1]["index"],
        "final": True,
        "normalizer_version": "v1",
        "harness_version": "claude-code-1.0",
        "turns": carried,
    }


def mock_hub_chunk_spec(work_ref: str) -> dict:
    """A scripted build -> deliver chunk the mock hub serves to a real runner."""
    return {
        "graph_id": "gr_service",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "session": "resume",
                "judged_by": "worker",
                "prompt": BUILD_SCRIPT,
                "judgement_prompt": JUDGEMENT_SCRIPT,
                "choices": [{"name": "pass", "description": "committed and green", "to": "deliver"}],
                "retries_max": 1,
            },
            "deliver": {
                "executor": "hub",
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo did not merge cleanly.", "to": "build"},
                        "failure": {"description": "A repo failed to land.", "to": "build"},
                    },
                },
            },
        },
        "work_refs": [{"source": "mock", "ref": work_ref}],
    }


#: The pool name ``build``/``review`` share — declaring the SAME ``session_name`` resumes the first node's minted session, the cross-node-resume shape this exercises.
OPENCODE_SESSION_POOL = "opencode-pool"

#: ``review``'s base turn: a no-op; the verdict comes from the judgement resume, not this.
OPENCODE_REVIEW_SCRIPT = "pass\n"
OPENCODE_REVIEW_JUDGEMENT = "verdict('pass', 'resumed the same OpenCode session; review complete')\n"


def mock_hub_opencode_chunk_spec(work_ref: str) -> dict:
    """A scripted build -> review -> done chunk run entirely under OpenCode.
    ``build`` and ``review`` share one ``session_name`` pool: ``build`` mints the pool's
    head fresh and resumes it for its own judgement; ``review`` then resumes that SAME
    session twice more — the cross-node resume the execution spec's OpenCode binding must serve."""
    return {
        "graph_id": "gr_service_opencode",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "session": "resume",
                "session_name": OPENCODE_SESSION_POOL,
                "session_harnesses": ["opencode"],
                "judged_by": "worker",
                "prompt": OPENCODE_BUILD_SCRIPT,
                "judgement_prompt": JUDGEMENT_SCRIPT,
                "choices": [{"name": "pass", "description": "committed and green", "to": "review"}],
                "retries_max": 1,
            },
            "review": {
                "executor": "runner",
                "session": "resume",
                "session_name": OPENCODE_SESSION_POOL,
                "session_harnesses": ["opencode"],
                "judged_by": "worker",
                "prompt": OPENCODE_REVIEW_SCRIPT,
                "judgement_prompt": OPENCODE_REVIEW_JUDGEMENT,
                "choices": [{"name": "pass", "description": "cold-eyes review: ready to land", "to": "done"}],
                "retries_max": 1,
            },
        },
        "work_refs": [{"source": "mock", "ref": work_ref}],
    }


@contextlib.contextmanager
def mock_hub(bin_dir: Path, port: int, *, log_dir: Path | None = None) -> Iterator[httpx2.Client]:
    """Run ``blizzard-mock-hub`` as a real subprocess and yield a client to it."""
    log = _mock_daemon_log("mock-hub", port, log_dir)
    proc = subprocess.Popen(
        [str(bin_dir / "blizzard-mock-hub"), "--host", "127.0.0.1", "--port", str(port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = mock_daemon_client(port)
    try:
        await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        terminate(proc)


@contextlib.contextmanager
def mock_runner(
    bin_dir: Path,
    port: int,
    hub_port: int,
    *,
    name: str = "runner-mock",
    token: str | None = None,
    log_dir: Path | None = None,
) -> Iterator[httpx2.Client]:
    """Run ``blizzard-mock-runner`` (a driver) pointed at a hub, and yield a client to it. With no
    ``token`` the driver adds itself at the hub on start, as ``runner init`` does; the id the hub minted
    reads back from its ``POST /_drive/register``."""
    hub_url = f"http://127.0.0.1:{hub_port}"
    log = _mock_daemon_log("mock-runner", port, log_dir)
    args = [
        str(bin_dir / "blizzard-mock-runner"),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--hub-url",
        hub_url,
        "--name",
        name,
    ]
    if token is not None:
        args += ["--token", token]
    proc = subprocess.Popen(
        args,
        env=runner_spawn_env(hub_url),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = mock_daemon_client(port)
    try:
        await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        terminate(proc)


@contextlib.contextmanager
def http_hub_client(port: int, *, token: str | None = None, name: str = "runner-parity") -> Iterator[HttpHubClient]:
    """A real :class:`HttpHubClient` pointed at a hub subprocess on ``port``, driven
    directly rather than through the runner loop — for a wire-parity assertion that needs
    a specific ``IHubClient`` method's response (see ``LoopWiring.tick_once`` for the
    behavioral-outcome alternative). It presents ``token``, else one for a runner it adds
    under ``name``, since the hub answers every tokenless fleet call ``401``."""
    base_url = f"http://127.0.0.1:{port}"
    if token is None:
        with httpx2.Client(base_url=base_url, timeout=15.0) as operator:
            _runner_id, token = add_runner(operator, name)
    client = httpx2.Client(base_url=base_url, timeout=15.0, headers={"Authorization": f"Bearer {token}"})
    try:
        yield HttpHubClient(client)
    finally:
        client.close()


def poll_until(predicate, *, timeout: float = 20.0, interval: float = 0.2) -> bool:
    """Poll ``predicate`` until true or the deadline; return whether it became true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


SECRET_ENV = "BZ_OAUTH_TEST_SECRET"
OAUTH_SECRET = "test-secret"
GARDEN_ROUTINE = "garden-service"
GARDEN_SCOPE = "garden-svc"


@contextlib.contextmanager
def oauth_hub(
    hub_dir: Path, port: int, *, providers: tuple[OAuthProviderConfig, ...], superuser: str | None = None
) -> Iterator[httpx2.Client]:
    """A real ``blizzard hub host`` subprocess, ``auth.mode = "oauth"`` with one or more
    configured providers — mirrors ``tests/fleet_launchers.py``'s own ``run_hub``,
    minus the work-source wiring this scenario does not need (login/`` /api/me`` alone).
    ``superuser`` sets ``auth.superuser`` so the bootstrap lifecycle runs at
    this real boot, same as any other deployment."""
    env = {**os.environ, SECRET_ENV: OAUTH_SECRET}
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    config = load_hub_config(hub_dir)
    config = dataclasses.replace(config, auth=AuthConfig(mode="oauth", oauth_providers=providers, superuser=superuser))
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
        yield client
    finally:
        client.close()
        terminate(proc)


def ingest(forge: httpx2.Client, hub: httpx2.Client, title: str) -> str:
    """File a forge issue (work-source seam) and ingest its pointer into a ready chunk."""
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post(
        "/api/chunks",
        json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]},
    )
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    # Ingest rests not-ready; promote so the chunk enters the ready queue.
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def stack(tmp_path: Path):
    """Stand up mock forge + real hub over a minted fixture's origins. Returns a context tuple."""
    bin_dir = require_mock_fleet()
    _workspace, origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    forge_port, hub_port = free_port(), free_port()
    return bin_dir, origins, forge_port, hub_port


@dataclass(frozen=True)
class Garden:
    """A real hub daemon with a garden routine seated on it, plus the mock runner that
    submits a run's artifacts and the fixture commit a delta's revisions cite."""

    hub: httpx2.Client
    hub_port: int
    runner: httpx2.Client
    routine_id: str
    nodes: dict[str, str]
    head: str


@contextlib.contextmanager
def garden_stack(tmp_path: Path) -> Iterator[Garden]:
    bin_dir = require_mock_fleet()
    _workspace, origins, bare = mint_world(bin_dir, tmp_path / "scratch")
    head = subprocess.run(
        ["git", "-C", str(bare), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    forge_port, hub_port = free_port(), free_port()
    with (
        run_forge(bin_dir, origins, forge_port),
        run_hub(tmp_path / "hub", forge_port, hub_port) as hub,
        mock_runner(bin_dir, free_port(), hub_port) as runner,
    ):
        minted = hub.post(
            "/api/graphs", json={"definition_yaml": yaml.safe_dump(PACKAGED.named("garden-routine").body)}
        )
        assert minted.status_code == 201, minted.text
        graph = hub.get(f"/api/graphs/{minted.json()['graph_id']}")
        assert graph.status_code == 200, graph.text
        nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}

        created = hub.post(
            "/api/routines",
            json={
                "name": GARDEN_ROUTINE,
                "graph_name": "garden-routine",
                "default_scope_slug": GARDEN_SCOPE,
                "default_model": [],
                "default_effort": None,
            },
        )
        assert created.status_code == 201, created.text
        assert runner.post("/_drive/register").json()["status"] == 201
        yield Garden(
            hub=hub,
            hub_port=hub_port,
            runner=runner,
            routine_id=created.json()["routine_id"],
            nodes=nodes,
            head=head,
        )


def deliver(
    g: Garden,
    ops: Sequence[dict[str, Any]],
    *,
    scope: str = GARDEN_SCOPE,
    proposals: Sequence[dict[str, Any]] = (),
    routine_id: str | None = None,
) -> httpx2.Response:
    """One routine run's whole delivery: mint the run, claim its entry node as the mock
    runner, submit the run's artifacts over the wire, then post the hub's own
    garden-delivery route. Returns that route's response — ``recorded`` or ``invalid``.
    ``routine_id`` defaults to ``g``'s own routine; a caller naming a second routine it
    minted itself sweeps that one instead."""
    run = g.hub.post(
        f"/api/routines/{routine_id or g.routine_id}/run",
        json={"scope_slug": scope, "mode": "full", "note": "sweep"},
    )
    assert run.status_code == 201, run.text
    chunk_id = run.json()["chunk_id"]
    assert g.hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert g.runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True

    delta = {
        "scope": scope,
        "revisions": {REPO_NAME: g.head},
        "measurement": "service sweep",
        "findings": list(ops),
    }
    artifacts = [
        {"name": "survey", "kind": "asset", "content": "{}", "attached": True},
        {"name": "delta", "kind": "asset", "content": json.dumps(delta), "attached": True},
    ]
    if proposals:
        artifacts.append({"name": "docket", "kind": "asset", "content": json.dumps(list(proposals)), "attached": True})
    completed = g.runner.post(
        "/_drive/complete", json={"chunk_id": chunk_id, "choice": "found", "artifacts": artifacts}
    ).json()
    assert completed["response"]["outcome"] == "next", completed

    epoch = g.hub.get(f"/api/chunks/{chunk_id}").json()["latest_epoch"]
    return g.hub.post(
        f"/api/chunks/{chunk_id}/garden-delivery",
        params={"node_id": g.nodes["deliver"], "epoch": epoch},
        json={"delta": ["delta"], "proposals": ["docket"] if proposals else []},
    )


def add_op(locus: str, *, introduced: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "add", "class": "stale-docstring", "locus": locus, "summary": f"weed at {locus}"}
    if introduced is not None:
        op["introduced"] = introduced
    return op


def seed(g: Garden, count: int, *, introduced: str | None = None) -> list[str]:
    """``count`` fresh live findings on the routine, newest-first ids as the read serves
    them — one delivery, one ``add`` per locus."""
    recorded = deliver(g, [add_op(f"src/app.py:{i}", introduced=introduced) for i in range(count)])
    assert recorded.status_code == 200 and recorded.json()["outcome"] == "recorded", recorded.text
    rows = live(g)
    assert len(rows) >= count
    return [r["finding_id"] for r in rows[:count]]


def live(g: Garden) -> list[dict[str, Any]]:
    resp = g.hub.get("/api/findings", params={"routine": GARDEN_ROUTINE, "scope": GARDEN_SCOPE})
    assert resp.status_code == 200, resp.text
    return resp.json()["findings"]
