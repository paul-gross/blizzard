"""Service-tier mock-fleet launchers and gate for out-of-process daemon and CLI tests.
The runner uses the mock hub; the hub uses the mock runner and forge. External-tool tests
use the emitted CLI surface (``bzh:external-cli-fake-is-service-tier``). Requires
``BLIZZARD_SERVICE=1`` and a provisioned sibling ``blizzard-mock`` worktree."""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import NotRequired, TypedDict

import httpx2
import pytest

from blizzard.runner.hub.internal.http_hub import HttpHubClient
from tests.fleet_launchers import await_http, mock_bin_dir, mock_daemon_client, terminate
from tests.fleet_world import REPO_NAME, scripted_build_prompt
from tests.runner_join import add_runner, runner_spawn_env
from tests.support import daemon_log_sink, shared_daemon_log_dir

# The service tier's own gate — independent of BLIZZARD_E2E so a run can select one tier.
SERVICE_ENABLED = os.environ.get("BLIZZARD_SERVICE") == "1"

service_gate = pytest.mark.skipif(
    not SERVICE_ENABLED,
    reason=(
        "service tier needs the mock fleet — a daemon's HTTP API or a mock-fleet CLI-surface "
        "binary; set BLIZZARD_SERVICE=1 (see tests/mock_fleet_support.py)"
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


class SegmentTurnPayload(TypedDict):
    """A ``TurnSegmentView`` as the wire carries it."""

    index: int
    kind: str
    timestamp: str | None
    text: str
    tool: str | None
    thinking_redacted: bool
    sidechain: str | None
    truncated: bool


class SegmentRecordPayload(TypedDict):
    """A ``TranscriptSegmentRecord`` as the wire carries it."""

    seq: int
    segment_id: str
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    turn_range_start: int
    turn_range_end: int
    final: bool
    normalizer_version: str
    harness_version: str
    turns: list[SegmentTurnPayload]


def transcript_segment_turn(index: int, kind: str, text: str) -> SegmentTurnPayload:
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
    turns: list[SegmentTurnPayload] | None = None,
) -> SegmentRecordPayload:
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


class ChoiceSpec(TypedDict):
    name: str
    description: str
    to: str


class RunnerNodeSpec(TypedDict):
    executor: str
    session: str
    judged_by: str
    prompt: str
    judgement_prompt: str
    choices: list[ChoiceSpec]
    retries_max: int
    session_model: NotRequired[list[str]]
    session_harnesses: NotRequired[list[str]]


class JudgementChoiceSpec(TypedDict):
    description: str
    to: str


class JudgementSpec(TypedDict):
    choices: dict[str, JudgementChoiceSpec]


class HubNodeSpec(TypedDict):
    executor: str
    judgement: JudgementSpec


class ChunkNodesSpec(TypedDict):
    build: RunnerNodeSpec
    deliver: HubNodeSpec


class WorkRefSpec(TypedDict):
    source: str
    ref: str


AnsweredFindingSpec = TypedDict(
    "AnsweredFindingSpec",
    {"finding_id": str, "routine_name": str, "scope_slug": str, "class": str, "locus": str, "summary": str},
)


class MockChunkSpec(TypedDict):
    """The body ``blizzard-mock-hub``'s ``POST /_seed/chunk`` takes."""

    graph_id: str
    entry: str
    nodes: ChunkNodesSpec
    work_refs: list[WorkRefSpec]
    garden_answered_findings: NotRequired[list[AnsweredFindingSpec]]
    project_id: NotRequired[str]


def mock_hub_chunk_spec(work_ref: str) -> MockChunkSpec:
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


@contextlib.contextmanager
def mock_hub(bin_dir: Path, port: int, *, log_dir: Path | None = None) -> Generator[httpx2.Client]:
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
) -> Generator[httpx2.Client]:
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
def http_hub_client(port: int, *, token: str | None = None, name: str = "runner-parity") -> Generator[HttpHubClient]:
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


def poll_until(predicate: Callable[[], bool], *, timeout: float = 20.0, interval: float = 0.2) -> bool:
    """Poll ``predicate`` until true or the deadline; return whether it became true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
