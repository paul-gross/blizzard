"""The kill-9 sweep (``blizzard:crash-sweep``) — MVP acceptance criterion 4.

For **every** crash point in the registry (``bzh:crash-point-registry``), this sweep:

1. runs a real ``build -> deliver`` mini-scenario over the mock fleet with hub + runner
   as real subprocesses;
2. arms the point so the owning daemon SIGKILLs itself the instant it reaches that
   boundary (a faithful ``kill -9``);
3. asserts the facts-level invariant checker (``bzh:invariant-checker``) is green over
   both stores immediately after the crash;
4. restarts the killed daemon **unarmed** (its startup pass is REAP) and lets the
   scenario converge;
5. asserts the chunk still lands **exactly once** — one ``delivery.landed`` fact, the
   file reachable from bare ``main`` exactly once — and the invariants are green again.

Two whole-process cases round it out: an external ``kill -9`` of the runner daemon
mid-flight, and a kill of the hub mid-delivery.

Gated like the e2e tier — needs the sibling ``blizzard-mock`` worktree, a local winter
source, and ``BLIZZARD_CRASH_SWEEP=1``; skipped otherwise (see ``conftest.py``). Run it::

    BLIZZARD_CRASH_SWEEP=1 uv run pytest -m crash_sweep
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.crash import discover_crash_points
from blizzard.foundation.store.invariants import check_invariants
from blizzard.hub.config import HubConfig
from blizzard.runner.config import RunnerConfig
from tests.crash.support import (
    REPO,
    CrashEnv,
    await_http,
    free_port,
    git_bare,
    graph_yaml,
    start_hub,
    start_runner,
    terminate,
    wait_death,
    wait_status,
    write_runner_config,
)

pytestmark = pytest.mark.crash_sweep

# Enumerated from the registry at collection — no hand-maintained point list (bzh:crash-point-registry).
_ALL_POINTS = [p.name for p in discover_crash_points()]

# A representative CI subset — one crash point per boundary family, biased toward the
# recovery-critical windows the sweep's two real bugs lived in: the FILL bind→claim window
# (chunk-strand recovery), the lost-ack replay (`flush.after-submit.before-ack`, hub
# idempotency), the per-repo land (delivery idempotency), and the mid-delivery hub crash
# (`deliver.before-terminal`, the `delivering`-strand recovery). Running the whole 22-point
# registry as real subprocesses is ~130s locally and multiples of that on a 2-core GitHub
# runner; the master `push` workflow sets BLIZZARD_CRASH_SWEEP_CI=1 to run this subset so the
# named gap is a REAL gate at bounded runtime, while the FULL sweep stays the documented
# local command (`mise run crash-sweep`) and the tag `release` workflow. The two whole-process
# cases below are never parametrized, so they run in both profiles.
_CI_SUBSET = (
    "reap.after-expire",
    "pull.after-flush",
    "fill.after-bind.before-claim",
    "spawn.after-lease-mint.before-spawn",
    "advance.after-buffer.before-flush",
    "flush.after-submit.before-ack",
    "deliver.after-repo-land",
    "deliver.before-terminal",
)


def _sweep_points() -> list[str]:
    """The crash points to parametrize: the full registry, or the CI subset under CI profile."""
    if os.environ.get("BLIZZARD_CRASH_SWEEP_CI") != "1":
        return _ALL_POINTS
    missing = [p for p in _CI_SUBSET if p not in _ALL_POINTS]
    # A subset point that no longer exists means the registry was renamed without updating the
    # CI selection — fail loudly rather than silently shrinking coverage (bzh:crash-point-registry).
    assert not missing, f"CI-subset crash points absent from the registry (renamed?): {missing}"
    chosen = set(_CI_SUBSET)
    return [p for p in _ALL_POINTS if p in chosen]


_POINTS = _sweep_points()


def _is_hub_point(point: str) -> bool:
    """Deliver points fire inside the hub's synchronous coordinator; the rest in the runner."""
    return point.startswith("deliver.")


def _assert_invariants(runner_dir: Path, hub_dir: Path, *, when: str) -> None:
    runner_db = RunnerConfig.load(runner_dir).db_url
    hub_db = HubConfig.load(hub_dir).db_url
    violations = check_invariants(runner_db_url=runner_db, hub_db_url=hub_db)
    assert not violations, f"invariant violations {when}:\n" + "\n".join(str(v) for v in violations)


def _ingest_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str) -> str:
    """Pre-mint the sweep graph, file a fresh issue, and ingest it to a ready chunk."""
    minted = hub.post("/api/graphs", json={"definition_yaml": graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a crash-sweep chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"pointers": [{"provider": "github", "url": f"{REPO}/issues/{number}"}]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


@pytest.mark.parametrize("point", _POINTS)
def test_kill9_at_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` at ``point`` recovers to a correct state and the chunk lands once."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    is_hub = _is_hub_point(point)

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point if is_hub else None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_chunk(hub, crash_env.forge, landed_file)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None if is_hub else point)

        # Wait for the ARMED daemon to reach its point and self-SIGKILL.
        armed = hub_proc if is_hub else runner_proc
        code = wait_death(armed)
        assert code == -9, f"armed daemon at {point} exited {code}, not SIGKILL (-9); point never reached?"

        # Invariant checker green right after the crash — the durable facts are consistent.
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")

        # Restart the killed daemon unarmed (startup = REAP first) and let it converge.
        if is_hub:
            hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
            await_http(hub, "/api/health", proc=hub_proc)
        else:
            runner_proc = start_runner(runner_dir, crash_point=None)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"

        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")

        # Exactly-once delivery: the file is reachable from bare main exactly once.
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


def test_kill9_runner_daemon_mid_flight(crash_env: CrashEnv, tmp_path: Path) -> None:
    """An external ``kill -9`` of the runner daemon while a chunk is in flight converges."""
    landed_file = "LANDED-runner-mid-flight.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # Let the chunk get claimed and in flight, then kill -9 the whole runner daemon.
        assert wait_status(hub, chunk_id, {"running", "delivering", "done"}) in {"running", "delivering", "done"}
        runner_proc.kill()
        runner_proc.wait(timeout=10)

        _assert_invariants(runner_dir, hub_dir, when="after external kill -9 of the runner daemon")

        runner_proc = start_runner(runner_dir, crash_point=None)
        assert wait_status(hub, chunk_id, {"done"}) == "done", "chunk did not converge after runner kill -9"
        _assert_invariants(runner_dir, hub_dir, when="after runner-daemon recovery")
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        assert len([ln for ln in tree.splitlines() if ln.strip()]) == 1
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


def test_kill9_hub_mid_delivery(crash_env: CrashEnv, tmp_path: Path) -> None:
    """A kill of the hub mid-delivery (before the terminal fact) resumes and lands once."""
    landed_file = "LANDED-hub-mid-delivery.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    point = "deliver.before-terminal"

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        assert wait_death(hub_proc) == -9, "hub did not reach the mid-delivery crash point"
        _assert_invariants(runner_dir, hub_dir, when="after kill of the hub mid-delivery")

        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)
        assert wait_status(hub, chunk_id, {"done"}) == "done", "chunk did not converge after hub mid-delivery kill"
        _assert_invariants(runner_dir, hub_dir, when="after hub mid-delivery recovery")
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        assert len([ln for ln in tree.splitlines() if ln.strip()]) == 1
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)
