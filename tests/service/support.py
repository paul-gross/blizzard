"""Shared scaffolding for the service tier — the mock-fleet launchers and gate.

The service tier exercises **one running daemon's HTTP API from outside the process**
with its counterpart bound to the mock fleet (blizzard-harness ``verification/blizzard.md``
test tiers): the runner against the **mock hub**, the hub against the **mock runner** +
the **mock forge**. Like the e2e tier it needs the sibling provisioned ``blizzard-mock``
worktree (whose venv ships ``blizzard-mock-hub`` / ``blizzard-mock-runner`` / ``-forge`` /
``-fixture`` / ``mock-claude-code``) and a local winter source, so it is **skipped unless
``BLIZZARD_SERVICE=1``** and those are present — the default gate stays hermetic.

It reuses the e2e module's process helpers (``_forge``, ``_hub``, ``_free_port``,
``_runner_config``, the fixture-workspace discovery) so both tiers stand the stack up the
same way; only the *counterpart* differs.
"""

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

from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO_NAME,
    _await_http,
    _mock_bin_dir,
    _terminate,
    _winter_source,
)


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


# The scripted build node: the prompt is the program. It commits a file to the
# toy-api worktree; the runner discovers the commit and pushes it to the bare file:// origin.
BUILD_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed by the mock harness\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    'subprocess.run(["git", "-C", repo, "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",'
    ' "commit", "-m", "feat: land a change from the mock harness"], check=True)\n'
)
JUDGEMENT_SCRIPT = "verdict('pass', 'the mock harness committed the change; checks are green')\n"


def mock_hub_chunk_spec(pm_ref: str) -> dict:
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
        "pm_pointers": [{"source": "mock", "ref": pm_ref}],
    }


@contextlib.contextmanager
def mock_hub(bin_dir: Path, port: int) -> Iterator[httpx.Client]:
    """Run ``blizzard-mock-hub`` as a real subprocess and yield a client to it."""
    proc = subprocess.Popen(
        [str(bin_dir / "blizzard-mock-hub"), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        _await_http(proc, client, "/api/health")
        yield client
    finally:
        client.close()
        _terminate(proc)


@contextlib.contextmanager
def mock_runner(bin_dir: Path, port: int, hub_port: int, *, runner_id: str = "runner-mock") -> Iterator[httpx.Client]:
    """Run ``blizzard-mock-runner`` (a driver) pointed at a hub, and yield a client to it."""
    env = {**os.environ, "BZ_HUB_URL": f"http://127.0.0.1:{hub_port}"}
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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        _await_http(proc, client, "/api/health")
        yield client
    finally:
        client.close()
        _terminate(proc)


class SseTap:
    """A background ``text/event-stream`` reader. Prefer the :func:`sse_tap` context manager."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.events: queue.Queue[str] = queue.Queue()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(20), "the hub's SSE stream never delivered its first line"

    def _run(self) -> None:
        with (
            httpx.Client(base_url=self.base_url, timeout=None) as client,
            client.stream("GET", "/api/events/stream") as resp,
        ):
            event_type: str | None = None
            for raw in resp.iter_lines():
                if not self._ready.is_set():
                    # Starlette sends ``http.response.start`` (what ``stream()`` above waits
                    # on) before it starts iterating the body, so headers arriving is not
                    # proof the broker subscription happened — that runs inside the body
                    # generator (see ``blizzard.hub.api.events._stream``). The first line on
                    # the wire, though, is only sent *after* ``broker.subscribe()`` runs, so
                    # it's the true readiness signal.
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
        self._stop.set()


@contextlib.contextmanager
def sse_tap(hub_port: int, *, settle: float = 2.0) -> Iterator[SseTap]:
    """A **live** SSE subscriber on the hub's ``/api/events/stream``, connected before the act.

    The component tier asserts event publication by reading the broker's *replay tail*
    (``emitted_events`` -> ``replay_since``). That proves an event was recorded, not that it
    was **delivered**: the live fan-out leg (publish -> subscriber queue -> wire) is exactly
    what a board watching the spine depends on, and a regression there is invisible to a
    replay-tail assertion. This taps the wire instead.

    Connects, then drains and discards whatever the broker replays on connect, so anything
    :meth:`SseTap.collect` reports afterwards is live fan-out rather than reconnect replay.
    """
    tap = SseTap(f"http://127.0.0.1:{hub_port}")
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
