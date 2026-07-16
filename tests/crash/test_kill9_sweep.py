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

RESUME's boundaries are the exception: they fire only on the first tick after a
*graceful* restart, which the ``build -> deliver`` scenario never performs, so they are
swept by the dedicated graceful-restart scenario (``test_kill9_at_resume_crash_point``)
which arms each on the restart process. The registry is partitioned accordingly.

Two whole-process cases round it out: an external ``kill -9`` of the runner daemon
mid-flight, and a kill of the hub mid-delivery.

Gated like the e2e tier — needs the sibling ``blizzard-mock`` worktree, a local winter
source, and ``BLIZZARD_CRASH_SWEEP=1``; skipped otherwise (see ``conftest.py``). Run it::

    BLIZZARD_CRASH_SWEEP=1 uv run pytest -m crash_sweep
"""

from __future__ import annotations

import contextlib
import os
import signal
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, select

from blizzard.foundation.crash import discover_crash_points
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.invariants import check_invariants
from blizzard.hub.config import HubConfig
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store import schema as runner_schema
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from tests.crash.support import (
    REPO,
    REPO_NAME,
    CrashEnv,
    await_http,
    build_script,
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

# RESUME's crash points fire only on the FIRST tick after a *graceful* restart, so the generic
# `build -> deliver` scenario below — which never restarts gracefully — can never reach them.
# Partition the registry: the generic sweep drives every non-resume boundary; the resume points
# are swept by the graceful-restart scenario further down (`test_kill9_at_resume_crash_point`).
_RESUME_POINTS = [p for p in _ALL_POINTS if p.startswith("resume.")]
_GENERIC_POINTS = [p for p in _ALL_POINTS if not p.startswith("resume.")]

# A representative CI subset — one crash point per boundary family, biased toward the
# recovery-critical windows the sweep's two real bugs lived in: the FILL bind→claim window
# (chunk-strand recovery), the lost-ack replay (`flush.after-submit.before-ack`, hub
# idempotency), the per-repo land (delivery idempotency), and the mid-delivery hub crash
# (`deliver.before-terminal`, the `delivering`-strand recovery). Running the whole 22-point
# generic sweep as real subprocesses is ~130s locally and multiples of that on a 2-core GitHub
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

# The resume CI subset: the recovery-critical kill-first window. The full graceful-restart
# sweep exercises all three resume boundaries; CI runs just this one to bound the added
# real-subprocess wall time (each resume case restarts the runner twice).
_RESUME_CI_SUBSET = ("resume.after-kill.before-reattach",)


def _select(points: list[str], ci_subset: tuple[str, ...]) -> list[str]:
    """The points to parametrize: all of ``points``, or its CI subset under the CI profile."""
    if os.environ.get("BLIZZARD_CRASH_SWEEP_CI") != "1":
        return points
    missing = [p for p in ci_subset if p not in points]
    # A subset point that no longer exists means the registry was renamed without updating the
    # CI selection — fail loudly rather than silently shrinking coverage (bzh:crash-point-registry).
    assert not missing, f"CI-subset crash points absent from the registry (renamed?): {missing}"
    chosen = set(ci_subset)
    return [p for p in points if p in chosen]


_POINTS = _select(_GENERIC_POINTS, _CI_SUBSET)
_RESUME_SWEEP = _select(_RESUME_POINTS, _RESUME_CI_SUBSET)


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
    # Ingest rests not-ready (D-103) — promote so the sweep's scenarios claim it as before.
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
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


# --------------------------------------------------------------------------- #
# Graceful restart-resume (issue #12) — re-attach to an in-flight session in place
# --------------------------------------------------------------------------- #


def _hanging_graph_yaml(landed_file: str) -> str:
    """A ``build -> deliver`` graph whose build commits, then ``hang()``s mid-flight.

    The commit lands before the worker blocks, so a graceful restart while it hangs has
    real in-flight work to resume; the build's judgement is a scripted ``pass`` the
    judgement resume emits after the session continues."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build_script(landed_file) + "hang()\n",
                "judgement": {
                    "prompt": "verdict('pass', 'committed before the restart; checks are green')\n",
                    "choices": {
                        "pass": {
                            "description": "The change is committed and the node's checks are green.",
                            "to": "deliver",
                        }
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {"executor": "hub", "mode": "merge-to-main"},
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_hanging_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str) -> str:
    """Mint the hanging graph and ingest a fresh issue against it to a ready chunk."""
    minted = hub.post("/api/graphs", json={"definition_yaml": _hanging_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a restart-resume chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"pointers": [{"provider": "github", "url": f"{REPO}/issues/{number}"}]})
    assert ingested.status_code == 201, ingested.text
    return ingested.json()["chunk_id"]


def _runner_store(runner_dir: Path) -> tuple[SqlAlchemyRunnerStore, Engine]:
    """A read store over the runner's sqlite plus its engine (dispose after use)."""
    engine = create_engine_from_url(RunnerConfig.load(runner_dir).db_url)
    return SqlAlchemyRunnerStore(engine), engine


def _leases_for_chunk(runner_dir: Path, chunk_id: str) -> list[tuple[str, int, str | None, int | None]]:
    """Every lease row (active or closed) for a chunk: (lease_id, epoch, session_id, pid)."""
    engine = create_engine_from_url(RunnerConfig.load(runner_dir).db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    runner_schema.leases.c.lease_id,
                    runner_schema.leases.c.epoch,
                    runner_schema.leases.c.session_id,
                    runner_schema.leases.c.pid,
                ).where(runner_schema.leases.c.chunk_id == chunk_id)
            ).all()
        return [(str(r[0]), int(r[1]), r[2], r[3]) for r in rows]
    finally:
        engine.dispose()


def _open_resume_intents(runner_dir: Path) -> set[str]:
    store, engine = _runner_store(runner_dir)
    try:
        return store.resume_intent_lease_ids()
    finally:
        engine.dispose()


def _await_committed(runner_dir: Path, chunk_id: str, landed_file: str, *, timeout: float = 30.0) -> None:
    """Block until the mid-flight build worker has made its commit in the bound worktree."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        store, engine = _runner_store(runner_dir)
        try:
            for binding in store.bindings_for_chunk(chunk_id):
                if (Path(binding.workdir) / REPO_NAME / landed_file).exists():
                    return
        finally:
            engine.dispose()
        time.sleep(0.2)
    raise AssertionError(f"build worker never committed {landed_file} before the graceful stop")


def test_graceful_restart_resumes_in_flight_session(crash_env: CrashEnv, tmp_path: Path) -> None:
    """A graceful runner restart re-attaches to its in-flight session in place (issue #12, D-082).

    The build worker commits and then hangs; a graceful stop (SIGTERM) marks its lease with a
    resume-intent, and the restart RESUMEs the *same* session — same lease/epoch/session, only
    the pid rewritten, no retry consumed — so the chunk lands **exactly once** rather than being
    redone under a fresh lease."""
    landed_file = "LANDED-restart-resume.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_hanging_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # Let the chunk get claimed and the worker reach its commit, then hang mid-flight.
        assert wait_status(hub, chunk_id, {"running"}) == "running"
        _await_committed(runner_dir, chunk_id, landed_file)

        # Gracefully stop the runner (SIGTERM): the shutdown hook marks the in-flight lease.
        terminate(runner_proc)
        before = _leases_for_chunk(runner_dir, chunk_id)
        assert len(before) == 1, f"expected one lease before restart, got {before}"
        lease_id, epoch, session_id, pid_before = before[0]
        assert session_id and pid_before is not None
        assert _open_resume_intents(runner_dir) == {lease_id}, "graceful shutdown did not mark a resume-intent"

        # Restart the runner: its first tick RESUMEs the marked session in place.
        runner_proc = start_runner(runner_dir, crash_point=None)
        assert wait_status(hub, chunk_id, {"done"}) == "done", "chunk did not converge after graceful restart"

        after = _leases_for_chunk(runner_dir, chunk_id)
        # Nothing worked twice: still exactly one lease, same lease/epoch/session — a same-lease
        # resume, not a retry (which would mint a new lease + epoch + session).
        assert len(after) == 1, f"restart-resume minted an extra lease (retry, not resume): {after}"
        r_lease_id, r_epoch, r_session_id, pid_after = after[0]
        assert (r_lease_id, r_epoch, r_session_id) == (lease_id, epoch, session_id)
        assert pid_after != pid_before, "the resumed process pid was not rewritten"
        # The intent was consumed by RESUME.
        assert _open_resume_intents(runner_dir) == set()

        _assert_invariants(runner_dir, hub_dir, when="after graceful restart-resume")
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --------------------------------------------------------------------------- #
# Ungraceful restart-resume (issue #13) — crash mid-work, no graceful marker
# --------------------------------------------------------------------------- #


def _session_ends(runner_dir: Path) -> set[str]:
    store, engine = _runner_store(runner_dir)
    try:
        return store.session_ended_lease_ids()
    finally:
        engine.dispose()


def test_kill9_runner_resumes_in_flight_session(crash_env: CrashEnv, tmp_path: Path) -> None:
    """An involuntary ``kill -9`` mid-build (no graceful marker) still re-attaches the session (issue #13, D-082).

    The graceful scenario's twin, crashed instead of stopped: the build worker commits then hangs,
    and a ``kill -9`` of the whole tree — the runner *and* its in-flight worker, a faithful reboot —
    skips the shutdown ``finally`` entirely, so **no resume-intent marker** is written. Startup
    crash-recovery must find the killed-mid-work lease itself (dead pid, no recorded session-end,
    heartbeat not stale) and route it to the *same* RESUME the graceful path uses, so the chunk
    lands **exactly once** under the same lease/epoch/session — only the pid rewritten — rather than
    being redone under a fresh retry. This is the acceptance criterion #12's marker could not cover:
    the case the systemd unit (``Restart=always``) actually exists for."""
    landed_file = "LANDED-crash-resume.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_hanging_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # Let the chunk get claimed and the worker reach its commit, then hang mid-flight.
        assert wait_status(hub, chunk_id, {"running"}) == "running"
        _await_committed(runner_dir, chunk_id, landed_file)
        before = _leases_for_chunk(runner_dir, chunk_id)
        assert len(before) == 1, f"expected one lease before the crash, got {before}"
        lease_id, epoch, session_id, pid_before = before[0]
        assert session_id and pid_before is not None

        # kill -9 the whole tree: the runner AND its hanging worker. The runner never runs its
        # shutdown finally, and the SIGKILL'd worker never fires its SessionEnd hook — so there is
        # neither a graceful resume-intent marker nor a session-end fact, exactly a reboot mid-run.
        runner_proc.kill()
        runner_proc.wait(timeout=10)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid_before, signal.SIGKILL)

        assert _open_resume_intents(runner_dir) == set(), "an ungraceful kill must leave no graceful marker"
        assert _session_ends(runner_dir) == set(), "a worker killed mid-work must record no session-end"
        _assert_invariants(runner_dir, hub_dir, when="after ungraceful kill -9 of the runner mid-build")

        # Restart: `host` runs startup crash-recovery (marks the killed-mid-work lease), then the
        # first tick's RESUME re-attaches the same session in place.
        runner_proc = start_runner(runner_dir, crash_point=None)
        assert wait_status(hub, chunk_id, {"done"}) == "done", "chunk did not converge after ungraceful restart"

        after = _leases_for_chunk(runner_dir, chunk_id)
        # Nothing worked twice: still exactly one lease, same lease/epoch/session — a same-lease
        # resume with no retry, reached with no graceful marker to hand it off.
        assert len(after) == 1, f"crash-resume minted an extra lease (retry, not resume): {after}"
        r_lease_id, r_epoch, r_session_id, pid_after = after[0]
        assert (r_lease_id, r_epoch, r_session_id) == (lease_id, epoch, session_id)
        assert pid_after != pid_before, "the resumed process pid was not rewritten"
        assert _open_resume_intents(runner_dir) == set(), "the crash resume-intent was not cleared after recovery"

        _assert_invariants(runner_dir, hub_dir, when="after ungraceful crash restart-resume")
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


@pytest.mark.parametrize("point", _RESUME_SWEEP)
def test_kill9_at_resume_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` at a RESUME boundary (armed on the restart) still re-attaches exactly once.

    The graceful-restart scenario, crashed mid-recovery: the worker commits then hangs, a graceful
    stop marks the lease, and the restart RESUMEs it — but this restart is ARMED at ``point`` so the
    runner SIGKILLs itself the instant RESUME reaches that boundary. A second, unarmed restart must
    still converge to ``done`` under the *same* lease/epoch/session, with the chunk landing exactly
    once and the invariant checker green. This is what closes the gap the plain
    ``test_graceful_restart_resumes_in_flight_session`` left: it proved the happy path, this proves
    every RESUME boundary the registry enumerates *recovers* from a crash, not just the clean case."""
    landed_file = f"LANDED-resume-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_hanging_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # Let the worker reach its commit and hang mid-flight, then gracefully stop to mark the lease.
        assert wait_status(hub, chunk_id, {"running"}) == "running"
        _await_committed(runner_dir, chunk_id, landed_file)
        terminate(runner_proc)
        before = _leases_for_chunk(runner_dir, chunk_id)
        assert len(before) == 1, f"expected one lease before restart, got {before}"
        lease_id, epoch, session_id, _pid_before = before[0]
        assert _open_resume_intents(runner_dir) == {lease_id}, "graceful shutdown did not mark a resume-intent"

        # Restart ARMED at the resume boundary: the first tick's RESUME reaches it and self-SIGKILLs.
        runner_proc = start_runner(runner_dir, crash_point=point)
        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")

        # Restart UNARMED: RESUME recovers and the chunk converges — exactly once, still one lease.
        runner_proc = start_runner(runner_dir, crash_point=None)
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"

        after = _leases_for_chunk(runner_dir, chunk_id)
        # Same-lease resume across the crash: no extra lease minted (that would be a retry), and the
        # lease/epoch/session are the ones marked before the restart — the pid is the only rewrite.
        assert len(after) == 1, f"resume across a crash at {point} minted an extra lease (retry): {after}"
        assert (after[0][0], after[0][1], after[0][2]) == (lease_id, epoch, session_id)
        assert _open_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
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
