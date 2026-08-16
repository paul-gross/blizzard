"""Shared scaffolding for the service tier — the mock-fleet launchers and gate.

Exercises one running daemon's HTTP API from outside the process with its counterpart
bound to the mock fleet: the runner against the mock hub, the hub against the mock
runner + forge. Skipped unless ``BLIZZARD_SERVICE=1`` and the sibling ``blizzard-mock``
worktree is provisioned — see ``verification/blizzard.md`` for the test tiers."""

from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from blizzard.runner.loop.internal.http_hub import HttpHubClient
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO_NAME,
    _await_http,
    _mock_bin_dir,
    _terminate,
    _winter_source,
)
from tests.support import daemon_log_sink, shared_daemon_log_dir


def mint_fixture(bin_dir: Path, winter_source: Path, scratch: Path) -> tuple[Path, Path, Path]:
    """Mint a fresh, disposable fixture world (bare origins + a winter workspace) and fence it.

    Returns ``(workspace, origins, origin_bare)``. Idempotent via ``reset`` — repeatable per run.
    """
    subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(winter_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    # Fence the tree so the mock harness will run (arbitrary code execution, gated on a marker).
    (workspace / ".blizzard-mock-harness-fence").write_text("service fence marker\n")
    return workspace, origins, origin_bare


# The service tier's own gate — independent of BLIZZARD_E2E so a run can select one tier.
SERVICE_ENABLED = os.environ.get("BLIZZARD_SERVICE") == "1"

service_gate = pytest.mark.skipif(
    not SERVICE_ENABLED,
    reason="service tier needs the mock fleet; set BLIZZARD_SERVICE=1 (see tests/service/support.py)",
)


def require_mock_fleet() -> Path:
    """The provisioned sibling ``blizzard-mock`` venv bin with the fleet binaries, or skip."""
    bin_dir = _mock_bin_dir()
    if (
        bin_dir is None
        or not (bin_dir / "blizzard-mock-hub").is_file()
        or not (bin_dir / "blizzard-mock-runner").is_file()
    ):
        pytest.skip(
            "no provisioned sibling blizzard-mock worktree with the mock hub/runner (run `winter provision <env>`)"
        )
    return bin_dir


def require_winter_source() -> Path:
    src = _winter_source()
    if src is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")
    return src


def require_stub_idp() -> Path:
    """The provisioned sibling ``blizzard-mock`` venv bin with the stub IdP (issue #92),
    or skip — a separate check from :func:`require_mock_fleet` since most service-tier
    scenarios need no OAuth counterpart at all."""
    bin_dir = _mock_bin_dir()
    if bin_dir is None or not (bin_dir / "blizzard-mock-idp").is_file():
        pytest.skip("no provisioned sibling blizzard-mock worktree with the stub IdP (run `winter provision <env>`)")
    return bin_dir


def _mock_daemon_log(name: str, port: int, log_dir: Path | None) -> Path:
    """Where one mock daemon's merged output goes (issue #145, ``bzh:daemon-stdout-to-file``).

    The mock fleet's daemons own no runtime directory, so ``log_dir`` defaults to
    :func:`~tests.support.shared_daemon_log_dir`; named by daemon and port so several
    concurrent instances of the same mock never share a file."""
    return (log_dir or shared_daemon_log_dir()) / f"{name}-{port}.log"


@contextlib.contextmanager
def stub_idp(bin_dir: Path, port: int, *, log_dir: Path | None = None) -> Iterator[httpx.Client]:
    """Run ``blizzard-mock-idp`` as a real subprocess and yield a client to it."""
    log = _mock_daemon_log("mock-idp", port, log_dir)
    proc = subprocess.Popen(
        [str(bin_dir / "blizzard-mock-idp"), "--host", "127.0.0.1", "--port", str(port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        _await_http(proc, client, "/healthz", log=log)
        yield client
    finally:
        client.close()
        _terminate(proc)


# The scripted build node: commits a file, pushes the branch, and declares it via
# `blizzard runner artifact commit` (issue #143).
BUILD_SCRIPT = (
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
                "run": [{"command": "true"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo did not merge cleanly.", "to": "build"},
                    },
                },
            },
        },
        "work_refs": [{"source": "mock", "ref": work_ref}],
    }


@contextlib.contextmanager
def mock_hub(bin_dir: Path, port: int, *, log_dir: Path | None = None) -> Iterator[httpx.Client]:
    """Run ``blizzard-mock-hub`` as a real subprocess and yield a client to it."""
    log = _mock_daemon_log("mock-hub", port, log_dir)
    proc = subprocess.Popen(
        [str(bin_dir / "blizzard-mock-hub"), "--host", "127.0.0.1", "--port", str(port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        _await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        _terminate(proc)


@contextlib.contextmanager
def mock_runner(
    bin_dir: Path, port: int, hub_port: int, *, runner_id: str = "runner-mock", log_dir: Path | None = None
) -> Iterator[httpx.Client]:
    """Run ``blizzard-mock-runner`` (a driver) pointed at a hub, and yield a client to it."""
    env = {**os.environ, "BZ_HUB_URL": f"http://127.0.0.1:{hub_port}"}
    log = _mock_daemon_log("mock-runner", port, log_dir)
    proc = subprocess.Popen(
        [
            str(bin_dir / "blizzard-mock-runner"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--hub-url",
            f"http://127.0.0.1:{hub_port}",
            "--runner-id",
            runner_id,
        ],
        env=env,
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        _await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        _terminate(proc)


@contextlib.contextmanager
def http_hub_client(port: int) -> Iterator[HttpHubClient]:
    """A real :class:`HttpHubClient` pointed at a mock-hub subprocess on ``port``, driven
    directly rather than through the runner loop — for a wire-parity assertion that needs
    a specific ``IHubClient`` method's response (see ``LoopWiring.tick_once`` for the
    behavioral-outcome alternative)."""
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        yield HttpHubClient(client)
    finally:
        client.close()


class SseTap:
    """A background ``text/event-stream`` reader. Prefer the :func:`sse_tap` context manager.
    ``last_event_id`` presents a resume cursor as the real ``Last-Event-ID`` header, the way a
    reconnecting browser does."""

    def __init__(self, base_url: str, *, last_event_id: int | None = None) -> None:
        self.base_url = base_url
        self.events: queue.Queue[str] = queue.Queue()
        self._headers = {} if last_event_id is None else {"Last-Event-ID": str(last_event_id)}
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._response: httpx.Response | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(20), "the daemon's SSE stream never delivered its first line"

    def _run(self) -> None:
        with (
            httpx.Client(base_url=self.base_url, timeout=None) as client,
            client.stream("GET", "/api/events/stream", headers=self._headers) as resp,
        ):
            self._response = resp
            event_type: str | None = None
            for raw in resp.iter_lines():
                if not self._ready.is_set():
                    # The first line on the wire, not headers alone, proves
                    # broker.subscribe() has actually run inside the body generator.
                    self._ready.set()
                if self._stop.is_set():
                    return
                line = raw.strip()
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and event_type:
                    self.events.put(event_type)
                    event_type = None

    def drain(self, *, settle: float = 1.5) -> list[str]:
        """Consume and return everything already queued — the broker's replay tail."""
        return self.collect(window=settle)

    def collect(self, *, window: float = 6.0) -> list[str]:
        """Every event type arriving within ``window`` seconds."""
        seen: list[str] = []
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            with contextlib.suppress(queue.Empty):
                seen.append(self.events.get(timeout=0.25))
        return seen

    def stop(self) -> None:
        # A flag alone leaves the reader thread parked in iter_lines() until the next
        # keepalive; closing the response aborts the read so stop() actually returns promptly.
        self._stop.set()
        if self._response is not None:
            with contextlib.suppress(Exception):
                self._response.close()


@contextlib.contextmanager
def sse_tap(port: int, *, settle: float = 2.0, last_event_id: int | None = None) -> Iterator[SseTap]:
    """A **live** SSE subscriber on the daemon under test's ``/api/events/stream``,
    connected before the act, proving an event was actually **delivered** rather than
    merely recorded. Drains and discards the broker's connect-time replay, so
    :meth:`SseTap.collect` reports only live fan-out. ``last_event_id`` resumes from a
    cursor (:class:`SseTap`)."""
    tap = SseTap(f"http://127.0.0.1:{port}", last_event_id=last_event_id)
    tap.start()
    try:
        tap.drain(settle=settle)
        yield tap
    finally:
        tap.stop()


def poll_until(predicate, *, timeout: float = 20.0, interval: float = 0.2) -> bool:
    """Poll ``predicate`` until true or the deadline; return whether it became true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
