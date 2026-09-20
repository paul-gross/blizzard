"""The mixed-harness lineage boundary, end to end (`bzh:e2e-node-sessions` phase 5).

One graph — `build` (the runner's own configured default, Claude Code, no `session`
declared) hands off to `opencode-review` (a graph-level named session pinned
`harnesses: [opencode]`, issue #144) — traverses BOTH harness lineages inside one
chunk's run, driven against a real `blizzard-hub`/`blizzard-runner` subprocess pair
(never `LoopWiring.tick_once()` in-process, unlike `test_acceptance_loop.py`; the
restart follows `test_runner_federation_e2e.py`'s subprocess shape instead).

The runner daemon is restarted twice, both CLEAN operator-style restarts (SIGTERM,
relaunch unarmed) — no crash point armed, since that recovery proof already belongs to
`tests/crash/test_kill9_sweep.py`'s OpenCode-lineage sweep (`bzh:crash-sweep` phase 4):

1. Right at the lineage boundary — after the hub records the transition from `build`
   into `opencode-review`, before the runner's own next tick would otherwise discover it
   and spawn the OpenCode worker. A RESUME turn does not re-run a node's own `prompt`
   text (`blizzard-mock`'s own engine sends a resume/judge turn a SEPARATE, shorter
   message, confirmed by hand while writing this scenario), so a restart that lands
   mid-way through `opencode-review`'s own FIRST turn — after it has spawned but before
   it committed+declared — can never recover its commit; landing this restart reliably
   BEFORE that first turn even starts therefore needs a real gap, not a race against the
   runner's own next tick. The FIRST start below runs on a deliberately long tick
   interval (`_start_runner`, `_BOUNDARY_TICK_SECONDS`) for exactly this reason — this
   test's own poll is of the RUNNER's own local store (`_wait_build_judged`, never the
   hub's `current_node_name` — see that helper's own docstring for why that read is too
   late), which lands mid-tick, well before the SAME tick's own remaining steps and the
   NEXT tick's own PULL/FILL would otherwise close `build`'s lease and spawn the OpenCode
   worker. The margin this affords is not a bare wall-clock guess: `PeriodicDriver._run`
   sleeps BETWEEN ticks on an interruptible `threading.Event.wait(interval)`, woken the
   instant a graceful SIGTERM sets it — so as long as this restart's own `terminate()`
   call is delivered and processed at any point before the full `_BOUNDARY_TICK_SECONDS`
   interval elapses (typically milliseconds after the SIGTERM lands, not a fixed-sleep
   coin flip), the next tick never starts at all, rather than racing to interrupt one
   already underway. `_BOUNDARY_TICK_SECONDS` is kept wide regardless, as defensive slack
   against a genuinely overloaded machine.
2. Mid-way through the OpenCode lineage's own session — the `opencode-review` worker
   commits, declares, and hangs; the runner is stopped gracefully (marking a
   resume-intent) and relaunched, which must RESUME the same lease/epoch/session rather
   than retry it (`tests/crash/test_kill9_sweep.py::test_graceful_restart_resumes_in_flight_session`'s
   own shape, reused here across the harness boundary instead of within one lineage). The
   runner reverts to the crash tier's own brisk tick (`tests.crash.support.start_runner`,
   `TICK_SECONDS`) for every start from here on, so the rest of the run is not needlessly
   slow.

Needs the sibling provisioned `blizzard-mock` worktree plus a local winter source; skips
without `BLIZZARD_E2E=1`."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.analytics.events import KIND_AGENT_SPAWN, KIND_SKILL_INVOCATION
from blizzard.runner.config import ENV_TRANSCRIPTS_ROOT, RunnerConfig
from blizzard.runner.store import schema as runner_schema
from tests.crash.support import (
    ENV_HARNESS_FENCE,
    LAND_STEP,
    REPO,
    REPO_NAME,
    await_http,
    build_script,
    forge_daemon,
    git_bare,
    mock_bin_dir,
    opencode_build_script,
    start_hub,
    start_runner,
    terminate,
    wait_status,
    winter_source,
    write_runner_config,
)
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.support import daemon_log_sink, free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e mixed-harness lineage needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

FIXTURE_ENV = "mixed-harness"

BUILD_LANDED_FILE = "LANDED-MIXED-BUILD.md"
REVIEW_LANDED_FILE = "LANDED-MIXED-OPENCODE.md"

#: The graph-level named session (issue #144) the `opencode-review` node resumes —
#: constrained to `harnesses: [opencode]` so the fresh mint that opens it is a real
#: OpenCode dispatch, mirroring `tests/crash/support.py::OPENCODE_SESSION_NAME`'s own
#: mechanism (this module's whole point is crossing the harness boundary WITHIN one
#: chunk's traversal, so it mints its own session name rather than sharing the crash
#: tier's). Declares its own `effort` ("high") so the two nodes' EFFORT provenance
#: genuinely differs, not just their harness.
_MIXED_SESSION_NAME = "mixed-harness-review"
_SESSION_EFFORT = "high"
#: The chunk-level default (`ChunkPatchRequest.default_effort`) `build` inherits, having
#: declared no session of its own (`EffectiveSession.of`'s declaration-over-chunk-default
#: fallback) — distinct from `_SESSION_EFFORT` so the two nodes' resolved efforts differ.
_CHUNK_DEFAULT_EFFORT = "medium"
#: Deliberately NOT a `blizzard:`-namespaced tier (`TIER_PREFIX`, `wire/envelope.py`): a
#: session whose EFFECTIVE model carries one arms `EligibilityCheck._lineage_satisfied`'s
#: strict per-tier check against EVERY reachable runner-executor node's own capability —
#: including `opencode-review`'s, reached from `build` (`_reachable_runner_nodes`'s own
#: whole-graph DFS) even while the chunk still sits at `build`. This runner's registered
#: `opencode` capability carries no `tiers` at all (the mock's own alias table is empty),
#: so a `blizzard:`-tier default here would deny EVERY claim outright — confirmed by hand
#: while writing this scenario. A native-looking model name for each node's own explicit
#: session declaration (see :data:`_SESSION_MODEL` below) sidesteps that gate entirely
#: while still giving each node its own genuinely distinct, non-fallback-derived model.
#: OpenCode's own adapter (unlike Claude Code's native-prefix/short-name vocabulary)
#: resolves a non-namespaced entry ONLY as a strict `provider/model` reference
#: (`OpenCodeModelReference.parse`) — anything else silently falls through to the
#: adapter's own configured default (empty in this runner's config), confirmed by hand
#: while writing this scenario (the resolved model on the lease, and the analytics event
#: derived from it, both came back blank until this session's model took that shape).
_CHUNK_DEFAULT_MODEL = ["claude-mixed-build"]
_SESSION_MODEL = ["mock-provider/opencode-mixed-review"]

#: Proven-dialect tool calls (mirrors `tests/service/test_mixed_harness_dispatch_service.py`'s
#: `_CLAUDE_SKILL_BUILD_SCRIPT`/`_OPENCODE_TASK_BUILD_SCRIPT`, D10, blizzard#439): the
#: OpenCode analytics dialect proves only `agent-spawn`, never an invented read/skill
#: mapping — so `opencode-review`'s own tool call is a `task`, never a `Skill`.
_BUILD_SKILL_NAME = "wf-mixed-review"
_REVIEW_AGENT_TYPE = "mixed-harness-reviewer"
_BUILD_TOOL_CALL = f"tool_call('Skill', {{'skill': {_BUILD_SKILL_NAME!r}}}, output='ran the commit skill')\n"
_REVIEW_TOOL_CALL = f"tool_call('task', {{'agent': {_REVIEW_AGENT_TYPE!r}}}, output='spawned a review sub-agent')\n"

_GIT_COMMIT_PRODUCES = [{"name": "commit", "kind": "git_commit"}]

#: The FIRST runner start's own tick interval — deliberately wide (seconds, not
#: `tests.crash.support.TICK_SECONDS`'s brisk 0.3s) so restart #1 has a real gap to land
#: in, rather than racing the runner's own next tick (see the module docstring's own D1).
#: Widened defensively past the minimum that machine ever needed, purely as slack against
#: an overloaded one — `PeriodicDriver`'s own interruptible between-tick wait (D1) is what
#: actually makes this non-racy; this constant only bounds how long that slack is.
_BOUNDARY_TICK_SECONDS = "8"


def _start_runner(runner_dir: Path, *, tick_seconds: str) -> subprocess.Popen[str]:
    """`tests.crash.support.start_runner`'s own body, parameterized on the tick interval
    instead of hardcoding its brisk crash-sweep cadence — this module's one genuine
    divergence from that helper's needs (see the module docstring's own D1). Never arms a
    crash point: every restart here is a clean operator-style SIGTERM, not a kill -9."""
    runner_bin = str(Path(sys.executable).parent / "blizzard-runner")
    env = {**os.environ, "BZ_RUNNER_TICK_SECONDS": tick_seconds, ENV_HARNESS_FENCE: "1"}
    return subprocess.Popen(
        [runner_bin, "host", "--dir", str(runner_dir)],
        env=env,
        stdout=daemon_log_sink(runner_dir / "daemon.log"),
        stderr=subprocess.STDOUT,
        text=True,
    )


def _mixed_graph_yaml(build_landed_file: str, review_landed_file: str) -> str:
    """One `build -> opencode-review -> deliver` graph: `build` is the runner's own
    configured default (no `session` declared at all), `opencode-review` resumes
    :data:`_MIXED_SESSION_NAME` — a graph-level named session pinned to OpenCode
    (issue #144) — so the SAME chunk's traversal crosses the harness lineage boundary
    once, inside one run, rather than two separate single-harness chunks."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "sessions": {
            _MIXED_SESSION_NAME: {"harnesses": ["opencode"], "effort": _SESSION_EFFORT, "model": _SESSION_MODEL}
        },
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_TOOL_CALL + build_script(build_landed_file),
                "produces": _GIT_COMMIT_PRODUCES,
                "judgement": {
                    "prompt": "verdict('pass', 'the claude code build committed the change; checks are green')\n",
                    "choices": {
                        "pass": {
                            "description": "The change is committed and the node's checks are green.",
                            "to": "opencode-review",
                        }
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "opencode-review": {
                "executor": "runner",
                "session": f"resume:{_MIXED_SESSION_NAME}",
                # `hang()` after the commit+push+declare (mirrors
                # `tests/crash/test_kill9_sweep.py::_opencode_hanging_graph_yaml`) — the
                # worker sits mid-flight so the SECOND restart lands inside an open session.
                "prompt": _REVIEW_TOOL_CALL + opencode_build_script(review_landed_file) + "hang()\n",
                "produces": _GIT_COMMIT_PRODUCES,
                "judgement": {
                    "prompt": "verdict('pass', 'the opencode review committed; checks are green')\n",
                    "choices": {
                        "pass": {
                            "description": "The opencode review is committed and the node's checks are green.",
                            "to": "deliver",
                        }
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": LAND_STEP}],
                "judgement": {
                    "choices": {
                        "success": {"description": "Delivered.", "to": "done"},
                        "failure": {"description": "Failed to deliver.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


# --------------------------------------------------------------------------- #
# Runner-store ground truth — mirrors tests/crash/test_kill9_sweep.py's own helpers,
# reimplemented locally rather than imported: this module's needs (a node-id/node-name
# join, a chunk-level effort cross-check) genuinely diverge from that module's private
# per-scenario helpers.


def _daemon_start_count(log_path: Path) -> int:
    """How many times this runner daemon process has started, read off its own
    ``"runner app created"`` startup banner (`blizzard/runner/app.py`) — append-mode
    (`daemon_log_sink`), so a restart's fresh banner is additional, never a replace. The
    concrete proof that a "restart" actually killed and relaunched a process, not a no-op."""
    if not log_path.exists():
        return 0
    return log_path.read_text(errors="replace").count("runner app created")


def _open_resume_intents(runner_dir: Path) -> set[str]:
    engine = create_engine_from_url(RunnerConfig.load(runner_dir).db_url)
    try:
        return SqlAlchemyRunnerStore(engine, runner_store_errors()).resume_intent_lease_ids()
    finally:
        engine.dispose()


def _await_committed(runner_dir: Path, chunk_id: str, landed_file: str, *, timeout: float = 30.0) -> None:
    """Block until the mid-flight `opencode-review` worker has committed **and durably
    declared** its git commit (issue #143) — the declaration, not the bare commit, is
    what a resume relies on to submit and land."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        engine = create_engine_from_url(RunnerConfig.load(runner_dir).db_url)
        try:
            store = SqlAlchemyRunnerStore(engine, runner_store_errors())
            committed = any(
                (Path(binding.workdir) / REPO_NAME / landed_file).exists()
                for binding in store.bindings_for_chunk(chunk_id)
            )
            lease = store.active_lease_for_chunk(chunk_id)
            declared = bool(lease and store.git_commit_declarations_for_lease(lease.lease_id))
            if committed and declared:
                return
        finally:
            engine.dispose()
        time.sleep(0.2)
    raise AssertionError(f"the opencode-review worker never committed+declared {landed_file} before the stop")


def _leases_with_node(runner_dir: Path, chunk_id: str) -> list[dict[str, object]]:
    """Every lease minted for ``chunk_id``, joined to its node identity and resolved
    model/effort (`lease_context`) — the join `tests/crash/test_kill9_sweep.py`'s own
    per-chunk helpers never need, since no crash-sweep scenario mixes two node names on
    one chunk."""
    engine = create_engine_from_url(RunnerConfig.load(runner_dir).db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    runner_schema.leases.c.lease_id,
                    runner_schema.leases.c.epoch,
                    runner_schema.leases.c.session_id,
                    runner_schema.leases.c.harness_id,
                    runner_schema.lease_context.c.node_id,
                    runner_schema.lease_context.c.node_name,
                    runner_schema.lease_context.c.resolved_model,
                    runner_schema.lease_context.c.resolved_effort,
                )
                .select_from(
                    runner_schema.leases.join(
                        runner_schema.lease_context,
                        runner_schema.leases.c.lease_id == runner_schema.lease_context.c.lease_id,
                    )
                )
                .where(runner_schema.leases.c.chunk_id == chunk_id)
            ).all()
        return [dict(r._mapping) for r in rows]
    finally:
        engine.dispose()


def _usage_rows(runner_dir: Path, chunk_id: str) -> list[dict[str, object]]:
    """The runner's own usage facts for ``chunk_id`` — the ground truth the hub-served
    board's `ChunkDetail.usage` is compared against below, rather than a hardcoded value
    this test would otherwise have to guess (e.g. a harness's own model-alias table)."""
    engine = create_engine_from_url(RunnerConfig.load(runner_dir).db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(runner_schema.usage_facts).where(runner_schema.usage_facts.c.chunk_id == chunk_id)
            ).all()
        return [dict(r._mapping) for r in rows]
    finally:
        engine.dispose()


def _wait_build_judged(runner_dir: Path, chunk_id: str, build_node_id: str, *, timeout: float = 60.0) -> None:
    """Poll the runner's OWN local store — not the hub, and not ``current_node_name`` — for
    ``build``'s own ``judge`` usage fact.

    This is the actual boundary restart #1 lands in. Watching the hub's own
    ``current_node_name`` instead is too late: by hand, closing `build`'s lease and
    spawning `opencode-review`'s fresh mint turned out to happen back-to-back within the
    SAME synchronous tick pass (a couple of milliseconds apart, regardless of tick
    length), so a poll that only reacts once the hub reports the flip always observes it
    after the opencode spawn has already been attempted — which, once interrupted before
    its first turn attaches a produce, can never be recovered (RESUME sends a short
    follow-up message, not a re-run of the original spawn's prompt).

    The judge step itself is different: the runner's own "elicitation" launches it within
    one tick, but only *discovers* its completion on a LATER tick (bounded by
    ``_BOUNDARY_TICK_SECONDS``) — and it's only on THAT later tick that the lease actually
    closes and the next node's fresh mint spawns. So the moment this judge usage fact
    lands, this test has a full tick interval of slack — no race — before either of those
    next happen."""
    deadline = time.monotonic() + timeout
    engine = create_engine_from_url(RunnerConfig.load(runner_dir).db_url)
    try:
        while time.monotonic() < deadline:
            with engine.connect() as conn:
                row = conn.execute(
                    select(runner_schema.usage_facts.c.id)
                    .where(runner_schema.usage_facts.c.chunk_id == chunk_id)
                    .where(runner_schema.usage_facts.c.node_id == build_node_id)
                    .where(runner_schema.usage_facts.c.kind == "judge")
                ).first()
            if row is not None:
                return
            time.sleep(0.05)
    finally:
        engine.dispose()
    raise AssertionError(f"build's own judge usage fact never landed for chunk {chunk_id}")


def test_mixed_lineage_crosses_a_harness_boundary_and_survives_two_operator_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One chunk's one traversal crosses from Claude Code into OpenCode; the runner
    daemon is cleanly restarted once at the lineage boundary and once more mid-session
    inside the OpenCode lineage, and every dispatch, the board, and analytics all
    attribute the right harness/model/effort/version to the right node throughout."""
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    source = winter_source()
    if source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    scratch = tmp_path / "scratch"
    minted = subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    assert minted.returncode == 0, f"fixture reset exited {minted.returncode}:\n{minted.stdout}\n{minted.stderr}"
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    assert workspace.is_dir() and (origins / f"{REPO_NAME}.git").is_dir(), "fixture mint did not lay out the tree"
    (workspace / ".blizzard-mock-harness-fence").write_text("mixed-harness e2e fence marker\n")

    transcripts_root = tmp_path / "transcripts"
    monkeypatch.setenv(ENV_TRANSCRIPTS_ROOT, str(transcripts_root))

    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    forge_port, hub_port, runner_port = free_port(), free_port(), free_port()

    hub_proc = None
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    runner_client = httpx.Client(base_url=f"http://127.0.0.1:{runner_port}", timeout=15.0)
    daemon_log = runner_dir / "daemon.log"
    try:
        with forge_daemon(bin_dir, origins, forge_port) as forge:
            hub_proc = start_hub(hub_dir, forge_port=forge_port, port=hub_port, crash_point=None)
            await_http(hub, "/api/health", proc=hub_proc)

            # 1. Mint the mixed graph and resolve each node's id (`GET/POST /api/graphs`'s
            # own `GraphView` — the wire shape this test's board/analytics assertions below
            # key their per-node grouping off).
            graph_resp = hub.post(
                "/api/graphs",
                json={"definition_yaml": _mixed_graph_yaml(BUILD_LANDED_FILE, REVIEW_LANDED_FILE)},
            )
            assert graph_resp.status_code == 201, graph_resp.text
            node_id_by_name = {n["name"]: n["node_id"] for n in graph_resp.json()["nodes"]}
            build_node_id = node_id_by_name["build"]
            review_node_id = node_id_by_name["opencode-review"]

            issue = forge.post(
                f"/repos/{REPO}/issues",
                json={"title": "mixed-harness lineage", "body": "crosses build -> opencode-review"},
            )
            assert issue.status_code == 201, issue.text
            number = issue.json()["number"]
            ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
            assert ingested.status_code == 201, ingested.text
            chunk_id = ingested.json()["chunk_id"]

            # `build` declares no session, so it inherits the chunk's own default effort
            # (`EffectiveSession.of`) — set BEFORE promote so both nodes' resolved efforts
            # are genuinely distinct: build -> the chunk default, opencode-review -> its
            # own named session's declared effort (`_SESSION_EFFORT`).
            patched = hub.patch(
                f"/api/chunks/{chunk_id}",
                json={"default_model": _CHUNK_DEFAULT_MODEL, "default_effort": _CHUNK_DEFAULT_EFFORT},
            )
            assert patched.status_code == 202, patched.text

            assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
            assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"

            # 2. Wire the runner with BOTH mock binaries and turn transcript shipping on —
            # the board's usage and the hub's derived analytics events both read off it.
            write_runner_config(runner_dir, workspace=workspace, bin_dir=bin_dir, hub_port=hub_port, port=runner_port)
            config = RunnerConfig.load(runner_dir)
            config = dataclasses.replace(
                config,
                transcripts_root=str(transcripts_root),
                transcripts_ship=True,
                worker_env_passthrough=(ENV_HARNESS_FENCE, ENV_TRANSCRIPTS_ROOT),
            )
            config.config_path.write_text(config.to_toml())

            # 3. Start the runner — on the wide boundary tick (see the module docstring's
            # own D1) — and let the Claude Code lineage (`build`) run to completion.
            runner_proc = _start_runner(runner_dir, tick_seconds=_BOUNDARY_TICK_SECONDS)
            await_http(runner_client, "/api/health", proc=runner_proc)
            assert _daemon_start_count(daemon_log) == 1, "the runner's own first start left no startup banner"

            # 4. Restart #1 — right at the lineage boundary: `build`'s own judge usage
            # fact is the earliest externally-observable signal that it has resolved (see
            # `_wait_build_judged`'s own docstring for why `current_node_name` is too
            # late), and this runner's wide (`_BOUNDARY_TICK_SECONDS`) tick interval gives
            # a full tick's worth of slack between that signal and the runner's own next
            # tick, which is what actually closes `build`'s lease and spawns the OpenCode
            # worker's fresh mint.
            _wait_build_judged(runner_dir, chunk_id, build_node_id, timeout=60.0)
            old_pid = runner_proc.pid
            terminate(runner_proc)
            assert runner_proc.poll() is not None, "restart #1: the runner did not actually exit"
            # Every start from here on reverts to the crash tier's own brisk tick.
            runner_proc = start_runner(runner_dir, crash_point=None)
            await_http(runner_client, "/api/health", proc=runner_proc)
            assert runner_proc.pid != old_pid, "restart #1: the daemon was not genuinely relaunched"
            assert _daemon_start_count(daemon_log) == 2, "restart #1 did not relaunch a fresh daemon process"

            # 5. Let the OpenCode lineage begin: the fresh mint spawns under `opencode`
            # (never a silent Claude Code fallback), commits, declares, then hangs.
            _await_committed(runner_dir, chunk_id, REVIEW_LANDED_FILE, timeout=60.0)
            mid_leases = [
                row for row in _leases_with_node(runner_dir, chunk_id) if row["node_name"] == "opencode-review"
            ]
            assert mid_leases, "the opencode-review node never got a lease after restart #1"
            assert all(row["harness_id"] == "opencode" for row in mid_leases), (
                f"the opencode-review node's fresh mint was not dispatched to opencode: {mid_leases}"
            )
            lease_id, epoch, session_id = mid_leases[0]["lease_id"], mid_leases[0]["epoch"], mid_leases[0]["session_id"]

            # 6. Restart #2 — mid-way through the SAME OpenCode session: a graceful stop
            # (SIGTERM) marks a resume-intent for the hung lease, proving this restart
            # lands INSIDE an already-open session, not merely after a fresh one starts.
            terminate(runner_proc)
            assert runner_proc.poll() is not None, "restart #2: the runner did not actually exit"
            assert _open_resume_intents(runner_dir) == {lease_id}, (
                "graceful shutdown before restart #2 did not mark the hung opencode-review lease for resume"
            )
            runner_proc = start_runner(runner_dir, crash_point=None)
            await_http(runner_client, "/api/health", proc=runner_proc)
            assert _daemon_start_count(daemon_log) == 3, "restart #2 did not relaunch a fresh daemon process"

            # 7. Let the graph run to completion.
            status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"}, timeout=90.0)
            assert status == "done", f"chunk did not converge to done after the two restarts (last status {status!r})"

            # --- Dispatch/session-continuity assertions -------------------------------

            all_leases = _leases_with_node(runner_dir, chunk_id)
            build_leases = [row for row in all_leases if row["node_name"] == "build"]
            review_leases = [row for row in all_leases if row["node_name"] == "opencode-review"]
            assert build_leases, "no lease was ever recorded for the build node"
            assert review_leases, "no lease was ever recorded for the opencode-review node"

            # No cross-harness leakage: every lease at each node carries the right adapter.
            assert all(row["harness_id"] == "claude_code" for row in build_leases), (
                f"a build-node lease was not recorded under the claude_code harness: {build_leases}"
            )
            assert all(row["harness_id"] == "opencode" for row in review_leases), (
                f"an opencode-review-node lease was not recorded under the opencode harness: {review_leases}"
            )

            # Restart #2 resumed the SAME lease/epoch/session across the crash — never a
            # retry (which would mint a fresh one).
            assert {row["lease_id"] for row in review_leases} == {lease_id}, (
                f"restart #2 minted an extra opencode-review lease (a retry, not a resume): {review_leases}"
            )
            assert {row["epoch"] for row in review_leases} == {epoch}
            assert {row["session_id"] for row in review_leases} == {session_id}
            assert _open_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"

            # Effort provenance, resolved distinctly per node (`EffectiveSession.of`):
            # build inherited the chunk default, opencode-review its own session's.
            assert {row["resolved_effort"] for row in build_leases} == {_CHUNK_DEFAULT_EFFORT}, build_leases
            assert {row["resolved_effort"] for row in review_leases} == {_SESSION_EFFORT}, review_leases

            # --- Exactly-once delivery: both nodes' commits landed on bare main --------

            for landed_file in (BUILD_LANDED_FILE, REVIEW_LANDED_FILE):
                tree = git_bare(origins / f"{REPO_NAME}.git", "log", "--oneline", "--", landed_file)
                commits = [line for line in tree.splitlines() if line.strip()]
                assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"

            # --- The board: ChunkDetail.usage carries the right harness/model/version per
            # node, cross-checked against the runner's own ground truth rather than a
            # hardcoded value this test would otherwise have to guess (a harness's own
            # model-alias table, for instance).

            detail = hub.get(f"/api/chunks/{chunk_id}").json()
            usage = detail["usage"]
            assert usage, "the board recorded no per-node-step usage at all"
            runner_usage = _usage_rows(runner_dir, chunk_id)
            runner_by_node: dict[str, list[dict[str, object]]] = {}
            for row in runner_usage:
                runner_by_node.setdefault(str(row["node_id"]), []).append(row)

            for node_id, node_label, expected_harness in (
                (build_node_id, "build", "claude_code"),
                (review_node_id, "opencode-review", "opencode"),
            ):
                board_rows = [u for u in usage if u["node_id"] == node_id]
                assert board_rows, f"the board carried no usage rows for {node_label}"
                assert all(u["harness_id"] == expected_harness for u in board_rows), (node_label, board_rows)
                ground_truth = runner_by_node.get(node_id, [])
                assert ground_truth, f"the runner itself recorded no usage_facts for {node_label}"
                assert {u["harness_id"] for u in board_rows} == {r["harness_id"] for r in ground_truth}, node_label
                assert {u["model"] for u in board_rows} == {r["model"] for r in ground_truth}, node_label
                board_versions = {u["harness_version"] for u in board_rows}
                ground_truth_versions = {r["harness_version"] for r in ground_truth}
                assert board_versions == ground_truth_versions, node_label
                if expected_harness == "opencode":
                    # A mirror-compare alone can't tell "both sides genuinely agree on
                    # {'1.18.25'}" apart from "both sides silently dropped version capture
                    # and agree on {None}" — OpenCode's own mock session-info document
                    # always carries a version (`_opencode_transcript.py`'s `_MOCK_VERSION`,
                    # confirmed by the dispatch-service test's own honest-asymmetry
                    # comment), so this side additionally pins a genuinely non-empty
                    # value, not merely one that agrees with the other side.
                    assert board_versions - {None, ""}, (node_label, board_versions)

            # --- Analytics: derive, then read the real events back per node.

            remaining = 1
            for _ in range(10):
                derived = hub.post("/api/analytics/re-derive", json={"chunk_id": chunk_id, "limit": 200})
                assert derived.status_code == 200, derived.text
                remaining = derived.json()["remaining"]
                if remaining == 0:
                    break
            assert remaining == 0, "analytics derivation never converged to zero remaining candidates"

            events_resp = hub.get("/api/analytics/events", params={"limit": 1000})
            assert events_resp.status_code == 200, events_resp.text
            events = [e for e in events_resp.json()["events"] if e["chunk_id"] == chunk_id]
            assert events, "no analytics events derived for the mixed-harness chunk"

            build_events = [e for e in events if e["node_id"] == build_node_id]
            review_events = [e for e in events if e["node_id"] == review_node_id]

            # `model` on an analytics event is the RESOLVED/REQUESTED model (the same value
            # `lease_context.resolved_model` carries), not the mock harness's own reported
            # usage figure — the mock claude-code binary always reports a fixed literal
            # regardless of what `--model` it was actually launched with (see
            # `blizzard_mock.harness.facades._usage.MOCK_MODEL`), which is exactly why the
            # board comparison above cross-checks `usage_facts` while this one instead
            # checks against the literal model each node's session declared/inherited.
            build_skill = [e for e in build_events if e["kind"] == KIND_SKILL_INVOCATION]
            assert build_skill and build_skill[0]["subject"] == _BUILD_SKILL_NAME, build_events
            assert all(e["harness_id"] == "claude_code" for e in build_skill), build_skill
            assert {e["effort"] for e in build_skill} == {_CHUNK_DEFAULT_EFFORT}, build_skill
            assert {e["model"] for e in build_skill} == set(_CHUNK_DEFAULT_MODEL), build_skill

            review_spawn = [e for e in review_events if e["kind"] == KIND_AGENT_SPAWN]
            assert review_spawn and review_spawn[0]["subject"] == _REVIEW_AGENT_TYPE, review_events
            assert all(e["harness_id"] == "opencode" for e in review_spawn), review_spawn
            assert {e["effort"] for e in review_spawn} == {_SESSION_EFFORT}, review_spawn
            assert {e["model"] for e in review_spawn} == set(_SESSION_MODEL), review_spawn
    finally:
        hub.close()
        runner_client.close()
        terminate(runner_proc)
        terminate(hub_proc)
