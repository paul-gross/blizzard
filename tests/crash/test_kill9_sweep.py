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

The ``abandon.*`` family is a second exception, for the same reason: the boundary is
reached only when the hub reassigns or detaches a chunk out from under an active
lease, which the plain ``build -> deliver`` scenario never triggers. It is
swept by its own dedicated scenario (``test_kill9_at_abandon_crash_point``), which
detaches a running chunk mid-flight via the real hub endpoint and proves the crash
recovers through RESUME, not through REAP's retry path (blizzard#38 slice 5).

The ``pause.*`` family is the third, and the abandon family's mirror image: its boundary
is reached only when an operator **pauses** a chunk out from under an active lease
(issue #46), and where the abandon gives the claim up, the pause keeps it. Its dedicated
scenario (``test_kill9_at_pause_park_crash_point``) pauses a running chunk mid-flight,
crashes the runner between the worker's kill and the durable park, and proves recovery
converges *because* RESUME parks a paused chunk rather than abandoning it.

The ``hubnode.`` family is the fourth (issue #65): its boundaries open only inside the
generic hub command node executor, which runs a ``run:`` node's declared commands
serialized fleet-wide on the transition-in completion — a shape the plain
``build -> deliver`` scenario (whose ``deliver`` node, since #67, is just ``run: [{command:
"true"}]``, no forge traffic) never mints. Its dedicated scenario
(``test_kill9_at_hub_command_node_crash_point``) drives a ``build -> merge(run:) -> done``
graph whose ``merge`` hub node lands the chunk to the mock forge across two ``produces:``
-marked steps, and crashes the hub in one of the two per-step windows: at
``hubnode.after-step.before-marker`` the just-run land step re-runs on recovery (re-merging
a merged head is a no-op), and at ``hubnode.after-marker.before-next`` only the unmarked
remainder re-runs (the marked land step is skipped). Either way the chunk lands exactly
once and the ``hub:one-live-exec-slot`` invariant is green with no leaked live slot.

Three whole-process cases round it out: an external ``kill -9`` of the runner daemon
mid-flight, and — closing the #67 gap the per-step registry cannot express — an external
``kill -9`` of the whole hub process group MID-SCRIPT, inside EITHER of the two packaged
deliver scripts' own between-repos window: ``land_default.py``
(``test_kill9_between_default_graph_repo_pushes``) and its PR-free sibling
``land_ff.py`` (``test_kill9_between_ff_graph_repo_pushes``, #123). Both scripts loop over
an arbitrary, chunk-dynamic number of repos inside ONE ``run:`` step, recording each
``merged/<repo>`` marker through the MID-RUN CALLBACK rather than the executor's static
per-step ``produces:`` — so each script's "between two repos' pushes" boundary is a
WALL-CLOCK race an external kill (of the hub daemon AND the land subprocess it spawned)
must land inside, never a named ``hubnode.*`` registry point (the registry arms points
inside blizzard's OWN process, never inside a spawned script). Each dedicated scenario
mints a 2-repo chunk against the real script, arms its test-only pause
(``BZ_HUB_LAND_TEST_PAUSE_SECONDS``) so the kill lands right after the first repo's
marker is durable, and asserts recovery re-runs the script and re-lands ONLY the
unmarked repo — each repo landing exactly once, no leaked exec slot (``land_default``
additionally opens exactly one PR apiece; ``land_ff`` opens none to double-check, so its
exactly-once proof reads straight off each bare repo's history).

Gated like the e2e tier — needs the sibling ``blizzard-mock`` worktree, a local winter
source, and ``BLIZZARD_CRASH_SWEEP=1``; skipped otherwise (see ``conftest.py``). Run it::

    BLIZZARD_CRASH_SWEEP=1 uv run pytest -m crash_sweep
"""

from __future__ import annotations

import contextlib
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, select

from blizzard.foundation.crash import discover_crash_points
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.invariants import check_invariants
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.enrollment import hash_token
from blizzard.hub.store import schema as hub_schema
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store import schema as runner_schema
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import NewLease
from tests.crash.support import (
    LAND_STEP,
    OWNER,
    REPO,
    REPO_NAME,
    CrashEnv,
    await_http,
    build_script,
    free_port,
    git_bare,
    graph_yaml,
    migrate_hub_source_yaml,
    migrate_hub_target_yaml,
    migrate_source_yaml,
    migrate_target_yaml,
    nudge_graph_yaml,
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
# Likewise, the `abandon.*` boundary fires only when the hub reassigns/detaches a chunk out from
# under an active lease, which the generic scenario never does either. Partition the registry:
# the generic sweep drives every remaining boundary; resume points are swept by the
# graceful-restart scenario (`test_kill9_at_resume_crash_point`) and abandon points by the
# dedicated detach scenario (`test_kill9_at_abandon_crash_point`), further down.
_DEDICATED_PREFIXES = ("resume.", "abandon.", "pause.", "hubnode.", "migrate.", "attach.", "nudge.")
_RESUME_POINTS = [p for p in _ALL_POINTS if p.startswith("resume.")]
_ABANDON_POINTS = [p for p in _ALL_POINTS if p.startswith("abandon.")]
_PAUSE_POINTS = [p for p in _ALL_POINTS if p.startswith("pause.")]
# The generic hub command node's per-step windows (#65) — `hubnode.*` fires inside the
# hub's synchronous ``HubNodeExecutor`` (a `run:` node runs on the transition-in
# completion, exactly as `deliver.*` fires inside the coordinator), so the generic
# `build -> deliver` scenario below (which never mints a `run:` node) cannot reach it
# either. Excluded from `_GENERIC_POINTS` for that reason and swept by its own dedicated
# scenario (`test_kill9_at_hub_command_node_crash_point`), which drives a
# `build -> merge(run:) -> done` graph whose `merge` hub node lands the chunk to the mock
# forge, then crashes the hub inside one of the two per-step windows — proving the
# at-least-once-per-step contract: `hubnode.after-step.before-marker` re-runs the just-run
# step (the land re-runs; re-merging a merged head is a no-op), and
# `hubnode.after-marker.before-next` re-runs only the UNMARKED remainder (the marked land
# step is skipped). Both converge exactly once with the `hub:one-live-exec-slot` invariant
# green and no leaked live slot.
#
# `hubnode.after-poll.` is a further, NARROWER carve-out within the `hubnode.` family
# (issue #66): its boundary opens only when a `run:` step reports the reserved
# `pending` outcome, which the two-step land/verify scenario above never mints (both
# steps always succeed). Folding it into `_HUBNODE_POINTS` would silently parametrize
# `test_kill9_at_hub_command_node_crash_point` against a point its own scenario can
# never reach. So it is swept by its own dedicated scenario
# (`test_kill9_at_hub_node_pending_crash_point`), which drives a
# `build -> merge(run:) -> done` graph whose `merge` hub node reports `pending` on its
# first poll (a durable poll-attempt fact, the fleet-wide slot still live) then lands on
# the next, and crashes the hub inside that between-polls window
# (`hubnode.after-poll.before-slot-release`): the poll fact is durable but the slot's
# release never ran. Recovery keeps polling — pending-ness is DERIVED from the poll fact,
# never in-memory — the same chunk reentrantly reclaims its own still-live slot on the
# next due poll, lands exactly once, and releases the slot: the `hub:one-live-exec-slot`
# invariant is green after the crash (one leaked live slot is still <= 1) and after
# convergence (no leaked slot at all).
_HUBNODE_PENDING_POINTS = [p for p in _ALL_POINTS if p.startswith("hubnode.after-poll.")]
_HUBNODE_POINTS = [p for p in _ALL_POINTS if p.startswith("hubnode.") and p not in _HUBNODE_PENDING_POINTS]
# `migrate.*` fires inside the HUB process (`ApplyService._apply_migration`, issue #90),
# only when a worker selects a cross-graph judgement choice — which the generic
# `build -> deliver` scenario (a single-graph graph) never mints. So it is a dedicated
# family swept by its own scenario (`test_kill9_at_migrate_crash_point`), which drives a
# two-graph `source --migrate--> triage-delivery` graph: the worker migrates, the hub
# crashes right after the atomic re-pin, and recovery re-queues + lands the chunk under the
# target graph exactly once with `hub:migration-pin-consistent` green.
_MIGRATE_POINTS = [p for p in _ALL_POINTS if p.startswith("migrate.")]
# `attach.*` fires inside the RUNNER's local attach endpoint (`AttachmentService.attach`,
# issue #113), an out-of-band HTTP write the generic `build -> deliver` sweep never drives —
# so it is a dedicated family swept by its own scenario (`test_kill9_at_attach_crash_point`),
# which stands up a real runner daemon, seeds a lease + its capability token, and makes the
# real `POST /api/leases/{id}/attachments` call: the runner records the row durably, crashes
# in the after-record window, and the attachment (with full provenance) survives against the
# same store — criterion 3's kill-9 durability. It needs no hub and no forge (the attach
# channel is loop-independent), so it stands up neither.
_ATTACH_POINTS = [p for p in _ALL_POINTS if p.startswith("attach.")]
# `nudge.*` fires inside the RUNNER's own ADVANCE step (`_advance_exited_worker`,
# issue #113 Phase 4), only when a node's `produces:` name has neither a pushed git
# commit nor an explicit attachment — a condition the plain `build -> deliver` graph
# above never creates (its `build` node declares no `produces:` at all). So it is a
# dedicated family swept by its own scenario (`test_kill9_at_nudge_crash_point`),
# which drives `nudge_graph_yaml`'s `build -> deliver` graph — identical but for one
# unattached `produces:` name on `build` — and crashes the RUNNER (never the hub;
# both windows are runner-local) in one of the two per-nudge windows:
# `nudge.after-fired-fact.before-resume` (the guard is durable, the resume that
# delivers the nudge has not run) and `nudge.after-resume.before-reassemble` (the
# resume returned, attachments not yet re-read). Either way the chunk still lands
# exactly once and `runner:nudge-at-most-once` is green — the resume the crash
# interrupted is never repeated on the recovering pass, because the guard fact is
# written before the resume runs, not after (see the call site in
# `runner/loop/steps.py` for why that ordering is what makes the property hold).
_NUDGE_POINTS = [p for p in _ALL_POINTS if p.startswith("nudge.")]
_GENERIC_POINTS = [p for p in _ALL_POINTS if not p.startswith(_DEDICATED_PREFIXES)]

# A representative CI subset — one crash point per boundary family, biased toward the
# recovery-critical windows the sweep's two real bugs lived in: the FILL bind→claim window
# (chunk-strand recovery) and the lost-ack replay (`flush.after-submit.before-ack`, hub
# idempotency). Running the whole generic sweep as real subprocesses is ~130s locally and
# multiples of that on a 2-core GitHub runner; the master `push` workflow sets
# BLIZZARD_CRASH_SWEEP_CI=1 to run this subset so the named gap is a REAL gate at bounded
# runtime, while the FULL sweep stays the documented local command (`mise run crash-sweep`)
# and the tag `release` workflow. The three whole-process cases below are never parametrized,
# so they run in both profiles.
_CI_SUBSET = (
    "reap.after-expire",
    "pull.after-flush",
    "fill.after-bind.before-claim",
    "spawn.after-lease-mint.before-spawn",
    "advance.after-buffer.before-flush",
    "flush.after-submit.before-ack",
    # `claim.*` (issue #84b) is a new boundary family within `_GENERIC_POINTS` — its
    # lone member is its own CI representative, so it never ships with zero CI-subset
    # coverage, the same convention `_ABANDON_CI_SUBSET`/`_PAUSE_CI_SUBSET` follow.
    "claim.after-persist.before-response",
)

# The resume CI subset: the recovery-critical kill-first window. The full graceful-restart
# sweep exercises all three resume boundaries; CI runs just this one to bound the added
# real-subprocess wall time (each resume case restarts the runner twice).
_RESUME_CI_SUBSET = ("resume.after-kill.before-reattach",)

# The abandon CI subset: `abandon.*` is a new boundary family (blizzard#38 slice 5) with exactly
# one point today — its lone member is that family's CI representative, so a new family never
# ships with zero CI coverage (bzh:crash-point-registry).
_ABANDON_CI_SUBSET = ("abandon.after-kill.before-release",)

# The pause CI subset: `pause.*` is a new boundary family (issue #46) whose lone member is that
# family's CI representative, exactly as `abandon.*`'s is. It earns CI time on its own merit
# rather than by symmetry: this window is the regression fence on the plan's central bug — the
# recovery it asserts converges *only* because `_resume_marked_lease` parks a paused chunk instead
# of abandoning it, so a regression there fails this case and nothing else in the sweep.
_PAUSE_CI_SUBSET = ("pause.after-kill.before-park",)

# The hub command node CI subset (#65): `hubnode.*` is a new boundary family whose
# first-declared member is its own CI representative, exactly as `abandon.`/`pause.` are —
# so this new window's registry entry never ships with zero CI-subset coverage, even
# ahead of the dedicated sweep scenario landing (see the Gap comment on `_HUBNODE_POINTS`
# above).
_HUBNODE_CI_SUBSET = ("hubnode.after-step.before-marker",)

# The hub-node pending CI subset (#66): `hubnode.after-poll.` is the narrower
# between-polls window carved out of the `hubnode.*` family above (a `run:` step's
# `pending` outcome, not a per-step land). Its lone member is its own CI representative,
# so this window ships with real CI coverage — never zero — exactly as the other new
# families do, and the `_select` rename-guard asserts the point still exists in the
# registry.
_HUBNODE_PENDING_CI_SUBSET = ("hubnode.after-poll.before-slot-release",)

# The migrate CI subset (#90): `migrate.*` is a new hub-side boundary family whose lone
# member — the after-record window — is its own CI representative, so this new window
# ships with real CI coverage (never zero), exactly as the other new families do; the
# `_select` rename-guard asserts the point still exists in the registry.
_MIGRATE_CI_SUBSET = ("migrate.after-record.before-response",)

# The attach CI subset (#113): `attach.*` is a new runner-side boundary family whose lone
# member — the after-record durability window — is its own CI representative, so this new
# window ships with real CI coverage (never zero), exactly as the other new families do; the
# `_select` rename-guard asserts the point still exists in the registry.
_ATTACH_CI_SUBSET = ("attach.after-record.before-response",)

# The nudge CI subset (#113): `nudge.*` is a new runner-side boundary family whose first-
# declared member — the fired-fact-before-resume window, the one the "at most one nudge"
# guarantee rests on — is its own CI representative, exactly as the other new families are,
# so this new window ships with real CI coverage (never zero); the `_select` rename-guard
# asserts the point still exists in the registry.
_NUDGE_CI_SUBSET = ("nudge.after-fired-fact.before-resume",)


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
_ABANDON_SWEEP = _select(_ABANDON_POINTS, _ABANDON_CI_SUBSET)
_PAUSE_SWEEP = _select(_PAUSE_POINTS, _PAUSE_CI_SUBSET)
_HUBNODE_SWEEP = _select(_HUBNODE_POINTS, _HUBNODE_CI_SUBSET)
_HUBNODE_PENDING_SWEEP = _select(_HUBNODE_PENDING_POINTS, _HUBNODE_PENDING_CI_SUBSET)
_MIGRATE_SWEEP = _select(_MIGRATE_POINTS, _MIGRATE_CI_SUBSET)
_ATTACH_SWEEP = _select(_ATTACH_POINTS, _ATTACH_CI_SUBSET)
_NUDGE_SWEEP = _select(_NUDGE_POINTS, _NUDGE_CI_SUBSET)


def test_ci_subset_covers_every_family(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every family prefix in the registry yields a non-empty CI-profile selection.

    ``_select`` only asserts a *named* CI-subset point still exists (catching a rename) — it
    never asserts the converse. The three named subsets (``_CI_SUBSET`` / ``_RESUME_CI_SUBSET`` /
    ``_ABANDON_CI_SUBSET``) are closed allowlists, so a new point added to an already-partitioned
    family (a fourth ``resume.*`` boundary, a second ``abandon.*`` one) is silently absent from
    CI: those families are unreachable by the generic sweep, so the new point gets zero coverage,
    with no failure to say so. This makes the "a new family never ships with zero CI coverage"
    claim (see ``_ABANDON_CI_SUBSET`` above) mechanical rather than aspirational.

    Forces the CI profile via ``monkeypatch`` rather than trusting the ambient environment, so
    this assertion holds whether it is run standalone or under ``mise run crash-sweep-ci`` — a
    fast, registry-only computation, not a sweep run, independent of ``crash_env`` and
    ``BLIZZARD_CRASH_SWEEP``.
    """
    monkeypatch.setenv("BLIZZARD_CRASH_SWEEP_CI", "1")
    families = {p.split(".", 1)[0] for p in _ALL_POINTS}
    assert families, "the crash-point registry is empty — nothing to partition"
    ci_selected = (
        set(_select(_GENERIC_POINTS, _CI_SUBSET))
        | set(_select(_RESUME_POINTS, _RESUME_CI_SUBSET))
        | set(_select(_ABANDON_POINTS, _ABANDON_CI_SUBSET))
        | set(_select(_PAUSE_POINTS, _PAUSE_CI_SUBSET))
        | set(_select(_HUBNODE_POINTS, _HUBNODE_CI_SUBSET))
        | set(_select(_HUBNODE_PENDING_POINTS, _HUBNODE_PENDING_CI_SUBSET))
        | set(_select(_MIGRATE_POINTS, _MIGRATE_CI_SUBSET))
        | set(_select(_ATTACH_POINTS, _ATTACH_CI_SUBSET))
        | set(_select(_NUDGE_POINTS, _NUDGE_CI_SUBSET))
    )
    uncovered = {family for family in families if not any(p.startswith(f"{family}.") for p in ci_selected)}
    assert not uncovered, f"registry families with zero CI-subset coverage: {sorted(uncovered)}"


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
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    # Ingest rests not-ready — promote so the sweep's scenarios claim it as before.
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


# `claim.*` fires inside the HUB process (`ClaimService._claim_locked`, issue #84b) —
# the one `_GENERIC_POINTS` family that arms the hub rather than the runner, since the
# generic `build -> deliver` scenario's claim is a hub-side POST /routes handler, not a
# runner-loop step. `test_kill9_at_crash_point` below reads this to pick which daemon
# to arm and restart.
_HUB_SIDE_GENERIC_PREFIXES = ("claim.",)


@pytest.mark.parametrize("point", _POINTS)
def test_kill9_at_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` at ``point`` recovers to a correct state and the chunk lands once."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    on_hub = point.startswith(_HUB_SIDE_GENERIC_PREFIXES)

    # Every point in `_GENERIC_POINTS` fires inside the runner loop, except the
    # `claim.*` family above, which fires inside the hub's claim handler — the
    # dedicated families (resume./abandon./pause./hubnode.) are excluded above, and
    # arm their own daemon in their own scenarios.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point if on_hub else None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        # For a `claim.*` point, ingest+promote alone leaves the chunk ready; the
        # runner's own FILL claim below — the same call every scenario makes on the
        # way to delivery — is what drives the hub into the armed window.
        chunk_id = _ingest_chunk(hub, crash_env.forge, landed_file)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None if on_hub else point)

        # Wait for whichever ARMED daemon reaches its point and self-SIGKILLs.
        code = wait_death(hub_proc if on_hub else runner_proc)
        assert code == -9, f"armed daemon at {point} exited {code}, not SIGKILL (-9); point never reached?"

        # Invariant checker green right after the crash — the durable facts are consistent.
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")

        # Restart the killed daemon unarmed (startup = REAP first, for the runner) and
        # let it converge.
        if on_hub:
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


def _ingest_migrate_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str) -> tuple[str, str]:
    """Mint the migrate target + source graphs, file a fresh issue, ingest + promote a
    chunk pinned to the source. Returns (chunk_id, target_graph_id)."""
    target = hub.post("/api/graphs", json={"definition_yaml": migrate_target_yaml(landed_file)})
    assert target.status_code == 201, target.text
    src = hub.post("/api/graphs", json={"definition_yaml": migrate_source_yaml()})
    assert src.status_code == 201, src.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a migrate crash-sweep chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id, target.json()["graph_id"]


@pytest.mark.parametrize("point", _MIGRATE_SWEEP)
def test_kill9_at_migrate_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` right after a cross-graph migration is recorded still recovers (#90).

    ``migrate.*`` fires inside the HUB (``ApplyService._apply_migration``): the worker at
    the source graph's ``build`` node selects the ``migrate`` choice, the hub records the
    migration atomically (graph/model re-pinned, route released, artifacts committed), then
    self-SIGKILLs before returning ``MIGRATED``. The claim under test: the runner's
    lost-ack replay re-derives ``MIGRATED`` via the ``accepted_migration`` probe (no second
    re-pin — ``hub:one-migration-per-node-epoch`` stays green), the chunk re-queues at the
    target graph's ``build`` node under the new pin (``hub:migration-pin-consistent``
    green), and a claim there runs it to ``done`` — landing the file on bare ``main``
    exactly once, its history spanning two graphs."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # Arm the HUB: the migrate window opens inside its completions handler, not the runner.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id, target_graph_id = _ingest_migrate_chunk(hub, crash_env.forge, landed_file)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # The runner claims, the worker migrates, and the hub self-SIGKILLs in the window.
        code = wait_death(hub_proc)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")

        # The migration is durable even though the MIGRATED response never returned.
        hub_engine = create_engine_from_url(HubConfig.load(hub_dir).db_url)
        with hub_engine.connect() as conn:
            migrations = conn.execute(
                select(hub_schema.chunk_migrations).where(hub_schema.chunk_migrations.c.chunk_id == chunk_id)
            ).all()
        assert len(migrations) == 1, "the migration fact was not durably recorded before the crash"

        # Restart the hub UNARMED; the runner's replayed completion re-derives MIGRATED and
        # the chunk re-queues + lands under the target graph.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")

        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert detail["graph_id"] == target_graph_id, "the chunk was not re-pinned to the target graph"
        assert len(detail["migrations"]) == 1, "the two-graph history is missing its migration step"

        # Exactly-once: the target graph's build is the only branch that lands the file.
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


def _ingest_migrate_hub_chunk(hub: httpx.Client, forge: httpx.Client, title: str) -> tuple[str, str]:
    """Mint the hub-landing migrate target + source graphs (issue #111), file a fresh
    issue, ingest + promote a chunk pinned to the source. Returns (chunk_id, target_graph_id)."""
    target = hub.post("/api/graphs", json={"definition_yaml": migrate_hub_target_yaml()})
    assert target.status_code == 201, target.text
    src = hub.post("/api/graphs", json={"definition_yaml": migrate_hub_source_yaml()})
    assert src.status_code == 201, src.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "a hub-landing migrate crash chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id, target.json()["graph_id"]


@pytest.mark.parametrize("point", _MIGRATE_SWEEP)
def test_kill9_at_migrate_crash_point_landing_on_a_hub_node(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` at the migrate window when the migration lands on a **hub** node
    (issue #111) still recovers — it must not wedge at ``delivering``.

    The sibling ``test_kill9_at_migrate_crash_point`` lands on a *runner* node: on the
    lost-ack replay the hub returns ``MIGRATED``, the runner releases its route, and the
    chunk re-queues ``ready`` for a fresh claim. A **hub-landing** migration is the harder
    case this scenario fences: the migration retains the route and the chunk derives
    ``delivering`` (never runner-claimable ``ready``), so recovery cannot come from a fresh
    claim — it must come from the **holding runner's ADVANCE poll** driving the landed hub
    node. The crash fires at ``migrate.after-record.before-response``, *before* the inline
    ``HubNodeExecutor.run`` in ``_apply_migration`` — so the inline dispatch is lost to the
    crash and only the retained route + the runner's ``hub-advance`` poll can carry the
    chunk to ``done``. A regression that released the route (or derived ``ready``) on this
    path would strand the chunk with nothing driving it, and ``wait_status`` below would
    time out at ``delivering`` rather than converge.

    The source ``build`` commits nothing (it hands the chunk off), so the landed hub node
    has no submitted branches to merge — ``LAND_STEP`` is a clean no-op that routes
    ``success -> done``. The assertion is therefore on **convergence and the retained-route
    derivation**, not a landed file: the chunk reaches ``done`` under the target graph, its
    history records exactly one migration onto the hub-executed node, and the invariants are
    green after the crash and after convergence."""
    title = f"HUB-MIGRATE-{point.replace('.', '_')}"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # Arm the HUB: the migrate window opens inside its completions handler, not the runner.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id, target_graph_id = _ingest_migrate_hub_chunk(hub, crash_env.forge, title)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # The runner claims, the worker migrates onto the hub node, and the hub self-SIGKILLs.
        code = wait_death(hub_proc)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after hub-landing kill at {point}")

        # The migration is durable even though the response never returned — and it landed on
        # the hub-executed node, so the chunk derives `delivering`, never `ready`.
        hub_engine = create_engine_from_url(HubConfig.load(hub_dir).db_url)
        with hub_engine.connect() as conn:
            migrations = conn.execute(
                select(hub_schema.chunk_migrations).where(hub_schema.chunk_migrations.c.chunk_id == chunk_id)
            ).all()
        assert len(migrations) == 1, "the migration fact was not durably recorded before the crash"

        # Restart the hub UNARMED; the retained route means the holding runner's ADVANCE poll
        # drives the landed hub node to `done` — no fresh claim, no re-queue.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", (
            f"hub-landing migration did not converge to done after kill at {point} (last {status!r}) — "
            "a `delivering` timeout here means the retained-route chunk wedged with nothing driving it"
        )
        _assert_invariants(runner_dir, hub_dir, when=f"after hub-landing convergence past {point}")

        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert detail["graph_id"] == target_graph_id, "the chunk was not re-pinned to the target graph"
        assert len(detail["migrations"]) == 1, "the two-graph history is missing its migration step"
        assert detail["migrations"][0]["to_graph_name"] == "triage-hub"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# The seeded lease's known plaintext token, and the artifact the worker attaches.
_ATTACH_TOKEN = "the-attach-lease-token"
_ATTACH_NAME = "review-findings"
_ATTACH_CONTENT = "the worker's explicit per-produces artifact\n"
_ATTACH_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("point", _ATTACH_SWEEP)
def test_kill9_at_attach_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` right after the runner records a worker attachment keeps it (issue #113 criterion 3).

    ``attach.*`` fires inside the RUNNER's local attach endpoint
    (``AttachmentService.attach``, behind ``POST /api/leases/{id}/attachments``), the instant
    ``record_attachment``'s single committed txn returns and before the ``200`` does. Unlike
    the generic ``build -> deliver`` sweep, the attach channel is an out-of-band HTTP write no
    loop step drives — and completion assembly that would read it back is Phase 3, not built
    here — so this dedicated scenario stands up a real runner daemon (no hub, no forge: the
    attach path is loop-independent), seeds a lease + its Phase-1 capability token directly,
    and makes the real attach call. The runner writes the row durably, self-SIGKILLs in the
    after-record window, and the claim under test is that the attachment — with full
    provenance — is still readable against the **same store** after the ungraceful death: the
    durable fact a later completion (and the recovering ADVANCE tick) re-derives via
    ``attachments_for_lease``.

    The seeded lease is **parked** so REAP — which ticks at startup and would otherwise expire
    an unspawned, pid-less lease (``steps.reap``) — leaves it be; ADVANCE skips a pid-less
    lease outright, so nothing else in the hub-less loop touches it. The park is scaffolding to
    keep the lease alive for the out-of-band write, not part of the property under test.
    """
    runner_dir = tmp_path / "runner"
    # Nothing listens on ``hub_port`` — the attach path never calls the hub; the loop's hub
    # polls just fail and are swallowed, and the local API serves regardless.
    hub_port, runner_port = free_port(), free_port()
    write_runner_config(
        runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
    )
    db_url = RunnerConfig.load(runner_dir).db_url

    # Seed a lease + its capability token, then park it, through a store the daemon does not
    # yet hold; dispose so the hosted daemon opens the sqlite file with no concurrent writer.
    engine = create_engine_from_url(db_url)
    store = SqlAlchemyRunnerStore(engine)
    store.record_lease(
        NewLease(
            lease_id="lease_attach",
            chunk_id="ch_attach",
            graph_id="gr_attach",
            node_id="nd_review",
            node_name="review",
            epoch=4,
            runner_id="runner-local",
            retries_max=2,
            created_at=_ATTACH_NOW,
        )
    )
    store.record_lease_token("lease_attach", hash_token(_ATTACH_TOKEN), _ATTACH_NOW)
    store.record_ask(
        lease_id="lease_attach",
        chunk_id="ch_attach",
        question_id="q_park",
        question="parked so REAP leaves the seeded lease be",
        options=[],
        session_id=None,
        asked_at=_ATTACH_NOW,
    )
    store.record_park(lease_id="lease_attach", chunk_id="ch_attach", question_id="q_park", parked_at=_ATTACH_NOW)
    engine.dispose()

    runner = httpx.Client(base_url=f"http://127.0.0.1:{runner_port}", timeout=30.0)
    runner_proc = start_runner(runner_dir, crash_point=point)
    try:
        await_http(runner, "/api/health", proc=runner_proc)

        # The runner records the attachment durably, then self-SIGKILLs before the response —
        # the client sees the killed connection, never a 200.
        with pytest.raises(httpx.HTTPError):
            runner.post(
                "/api/leases/lease_attach/attachments",
                json={"name": _ATTACH_NAME, "content": _ATTACH_CONTENT},
                headers={"X-Blizzard-Lease-Token": _ATTACH_TOKEN},
            )
        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"

        # Durable across the kill -9: reopen the same store — the attachment and its full
        # provenance (lease/chunk/node/epoch/name) are exactly what the worker submitted,
        # though the 200 never returned.
        engine2 = create_engine_from_url(db_url)
        try:
            assert SqlAlchemyRunnerStore(engine2).attachments_for_lease("lease_attach") == {
                _ATTACH_NAME: _ATTACH_CONTENT
            }
            with engine2.connect() as conn:
                rows = conn.execute(
                    select(runner_schema.attachments).where(runner_schema.attachments.c.lease_id == "lease_attach")
                ).all()
            assert len(rows) == 1, "the attachment was not durably recorded before the crash"
            row = rows[0]._mapping
            assert (row["chunk_id"], row["node_id"], row["epoch"], row["name"]) == (
                "ch_attach",
                "nd_review",
                4,
                _ATTACH_NAME,
            ), "the attachment's provenance did not survive intact"
        finally:
            engine2.dispose()

        # The invariant checker is green over the durable runner facts right after the crash.
        violations = check_invariants(runner_db_url=db_url)
        assert not violations, "invariant violations after the attach crash:\n" + "\n".join(str(v) for v in violations)

        # Restart the runner UNARMED; the attachment is still readable against the same store —
        # the fact a later completion (Phase 3) prefers over the judgement assessment.
        runner_proc = start_runner(runner_dir, crash_point=None)
        await_http(runner, "/api/health", proc=runner_proc)
        engine3 = create_engine_from_url(db_url)
        try:
            assert SqlAlchemyRunnerStore(engine3).attachments_for_lease("lease_attach") == {
                _ATTACH_NAME: _ATTACH_CONTENT
            }
        finally:
            engine3.dispose()
    finally:
        runner.close()
        terminate(runner_proc)


def _ingest_nudge_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str) -> str:
    """:func:`_ingest_chunk`'s twin, minting :func:`nudge_graph_yaml` instead of
    :func:`graph_yaml` — the one unattached ``produces:`` name is what opens the
    `nudge.*` windows this scenario arms."""
    minted = hub.post("/api/graphs", json={"definition_yaml": nudge_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a nudge crash-sweep chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


@pytest.mark.parametrize("point", _NUDGE_SWEEP)
def test_kill9_at_nudge_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` at a `nudge.*` window recovers with the nudge fired at most once
    and the chunk still landing exactly once (issue #113, Phase 4).

    ``nudge.*`` fires inside the RUNNER's own ADVANCE step, so — unlike ``attach.*`` —
    this scenario needs a real hub too (the fact this window's condition depends on is
    an unattached ``produces:`` name on a real node-step, elicited through a real
    judgement resume against the mock harness). It always arms the runner, never the
    hub: both windows are runner-local. The mock worker never attaches
    ``NUDGE_PRODUCES_NAME`` in response to the nudge (scripting a conditional reply is
    not what these points need proven), so the completion that eventually lands
    carries the assessment fallback for it — the same shape an unnudged pass would
    produce, proving the crash cost the attempt nothing but the interrupted resume
    itself.
    """
    landed_file = f"NUDGE-LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_nudge_chunk(hub, crash_env.forge, landed_file)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=point)

        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"

        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")

        runner_proc = start_runner(runner_dir, crash_point=None)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"

        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")

        # Exactly-once delivery, as every scenario asserts.
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


def _ingest_hanging_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str) -> str:
    """Mint the hanging graph and ingest a fresh issue against it to a ready chunk."""
    minted = hub.post("/api/graphs", json={"definition_yaml": _hanging_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a restart-resume chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    # Ingest rests not-ready — promote so the resume scenarios claim it as before.
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


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
    """A graceful runner restart re-attaches to its in-flight session in place (issue #12).

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
    """An involuntary ``kill -9`` mid-build (no graceful marker) still re-attaches the session (issue #13).

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


# --------------------------------------------------------------------------- #
# Live detach recovery (blizzard#38) — the abandon crash point
# --------------------------------------------------------------------------- #


def _hang_once_build_script(landed_file: str, marker: Path) -> str:
    """Commit ``landed_file``, then ``hang()`` — but only the *first* time.

    Identical to :func:`build_script` plus a ``hang()`` gated on ``marker``: the first
    attempt (no marker yet) hangs mid-flight, which is the window this scenario detaches
    the chunk in; a fresh re-claim after the abandon runs the same script again in a new
    workdir, finds the marker, and returns normally so the chunk can actually reach
    ``done`` instead of hanging forever a second time."""
    return (
        "import pathlib, subprocess\n"
        f"repo = {REPO_NAME!r}\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        f"(pathlib.Path(repo) / {landed_file!r}).write_text('landed by the crash sweep\\n')\n"
        'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
        "subprocess.run(\n"
        '    ["git", "-C", repo,\n'
        '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
        '     "commit", "-m", "feat: land a change from the crash sweep"],\n'
        "    check=True,\n"
        ")\n"
        "if not marker.exists():\n"
        "    marker.write_text('hung once\\n')\n"
        "    hang()\n"
    )


def _abandon_graph_yaml(landed_file: str, marker: Path) -> str:
    """The hang-once ``build -> deliver`` graph this scenario detaches mid-flight."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _hang_once_build_script(landed_file, marker),
                "judgement": {
                    "prompt": "verdict('pass', 'committed before the detach; checks are green')\n",
                    "choices": {
                        "pass": {
                            "description": "The change is committed and the node's checks are green.",
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


def _ingest_abandon_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str, marker: Path) -> str:
    """Mint the hang-once graph and ingest a fresh issue against it to a ready chunk."""
    minted = hub.post("/api/graphs", json={"definition_yaml": _abandon_graph_yaml(landed_file, marker)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "an abandon-crash chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # rests not-ready otherwise
    return chunk_id


def _await_marker(marker: Path, *, timeout: float = 30.0) -> None:
    """Block until ``marker`` exists — proof the first attempt reached its hang, past the commit.

    Waiting on the committed file alone (:func:`_await_committed`) is not enough here: the file
    is written *before* the commit, so a detach racing in right after would kill the worker before
    it ever reaches the ``hang()`` line — leaving the marker unwritten, so the fresh re-claim
    would hang all over again instead of landing. Waiting on the marker pins the detach to
    strictly after the point the first attempt is actually parked at."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            return
        time.sleep(0.2)
    raise AssertionError(f"the build worker never reached its hang-once marker ({marker}) before the timeout")


def _closure_reason(runner_dir: Path, lease_id: str) -> str | None:
    """The closure reason recorded for ``lease_id``, or ``None`` if it is still active."""
    engine = create_engine_from_url(RunnerConfig.load(runner_dir).db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(runner_schema.lease_closures.c.reason).where(runner_schema.lease_closures.c.lease_id == lease_id)
            ).first()
        return str(row[0]) if row is not None else None
    finally:
        engine.dispose()


def _wait_for_closure(runner_dir: Path, lease_id: str, *, timeout: float = 30.0) -> str | None:
    """Poll until ``lease_id`` closes, or the timeout elapses (return whatever was last seen)."""
    deadline = time.monotonic() + timeout
    reason = None
    while time.monotonic() < deadline:
        reason = _closure_reason(runner_dir, lease_id)
        if reason is not None:
            return reason
        time.sleep(0.25)
    return reason


@pytest.mark.parametrize("point", _ABANDON_SWEEP)
def test_kill9_at_abandon_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` right after the abandon's kill (worker dead, envs still held) still recovers.

    A live operator detach is the new way this window is reached (blizzard#38 slice 5):
    the chunk is claimed and hung mid-flight, the operator detaches it via the real hub endpoint,
    and the armed runner's next PULL discovers the detach, kills the hung worker, and self-SIGKILLs
    at ``point`` before the environments are released. The claim under test
    is that this is recovered by the **same** path restart-resume already carries: the dead pid's
    heartbeat is fresh at crash time, so the startup scan marks it for resume rather than reaping
    it as stalled; RESUME then re-asks the hub, finds the chunk still not ours, and re-runs the
    abandon idempotently. The original lease must close ``released`` — not ``reaped`` — which is
    exactly the distinction that would catch REAP's expire path retrying the chunk instead of
    releasing it. The chunk is then re-claimable and lands exactly once."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    marker = tmp_path / "hang-once.marker"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_abandon_chunk(hub, crash_env.forge, landed_file, marker)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        # Armed from the start: unarmed in effect until a live PULL discovers the detach and
        # reaches `point` inside the abandon it triggers — the claim + spawn + commit + hang
        # happen normally first.
        runner_proc = start_runner(runner_dir, crash_point=point)

        assert wait_status(hub, chunk_id, {"running"}) == "running"
        # Wait for the marker, not just the committed file: the file is written before the
        # commit, so racing the detach in right after it appears could kill the worker before it
        # ever reaches `hang()` — see `_await_marker`.
        _await_marker(marker)
        before = _leases_for_chunk(runner_dir, chunk_id)
        assert len(before) == 1, f"expected one lease before detach, got {before}"
        lease_id_before, _epoch_before, _session_before, pid_before = before[0]
        assert pid_before is not None
        assert _session_ends(runner_dir) == set(), "the hung worker must not have declared done yet"

        # The operator detaches the running chunk — a live route release, not a requeue.
        detached = hub.post(f"/api/chunks/{chunk_id}/detach")
        assert detached.status_code == 202, detached.text
        assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready", "detach did not release the route"

        # The armed runner's next PULL learns of the detach, kills the hung worker, and self-SIGKILLs
        # at `point` before the environments are released.
        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")
        # The kill (not the mock harness's own SessionEnd hook) is what ended the worker — a
        # SIGKILL is uncatchable, so no session-end fact was recorded for it.
        assert _session_ends(runner_dir) == set(), "a SIGKILL'd worker must record no session-end"

        # Restart UNARMED: startup crash-recovery must read the dead pid's fresh-at-crash heartbeat
        # as resumable (not stale), mark it, and RESUME must re-run the abandon idempotently.
        runner_proc = start_runner(runner_dir, crash_point=None)
        reason = _wait_for_closure(runner_dir, lease_id_before)
        assert reason == "released", (
            f"the original lease closed {reason!r}, not 'released' — the abandon window was not "
            "recovered via RESUME (a REAP-retry here would consume a retry instead of releasing)"
        )
        assert _open_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"

        # Re-claimable: the same (only) runner picks the now-ready chunk back up fresh and, this
        # time past the marker, runs it to completion rather than hanging again.
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"
        after = _leases_for_chunk(runner_dir, chunk_id)
        assert len(after) == 2, f"expected the original (released) lease plus one fresh re-claim: {after}"
        lease_ids_after = {row[0] for row in after}
        assert lease_id_before in lease_ids_after
        fresh_lease_id = next(lid for lid in lease_ids_after if lid != lease_id_before)
        assert _closure_reason(runner_dir, fresh_lease_id) == "transitioned", "the fresh re-claim did not land cleanly"

        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --------------------------------------------------------------------------- #
# Operator chunk pause (issue #46) — the pause-park crash point
# --------------------------------------------------------------------------- #


def _open_pause_parks(runner_dir: Path) -> set[str]:
    store, engine = _runner_store(runner_dir)
    try:
        return store.pause_parked_lease_ids()
    finally:
        engine.dispose()


@pytest.mark.parametrize("point", _PAUSE_SWEEP)
def test_kill9_at_pause_park_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` between a paused worker's kill and its durable park still keeps the claim.

    The abandon scenario's inverse (issue #46). The operator pauses a chunk that is hung
    mid-flight; the armed runner's PULL discovers the pause, kills the worker, and self-SIGKILLs
    *before* the ``pause_parks`` row is durable — leaving a lease that is active,
    session-bearing, pid-dead and unparked.

    That residue is the whole point. It is exactly the shape startup crash-recovery marks for
    resume, so recovery runs straight back through ``_resume_marked_lease`` — which converges
    **only because** it reads ``detail.pause`` and parks, killing an already-dead pid as a no-op.
    The same path *abandoned* the chunk before this issue's central fix, so a regression there
    surfaces right here as a ``released`` closure and freed environments instead of a held claim.
    That is what earns this point its place in the CI subset.

    The claim is then proven end to end: the chunk stays paused with its environments held, the
    operator resumes it, the *same* session finishes the work, and it lands exactly once under
    the same lease — no retry consumed anywhere.
    """
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        # The hanging graph: the worker commits, then hangs — real in-flight work to pause.
        chunk_id = _ingest_hanging_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        # Armed from the start: unarmed in effect until a live PULL discovers the pause and
        # reaches `point` inside the park it triggers.
        runner_proc = start_runner(runner_dir, crash_point=point)

        assert wait_status(hub, chunk_id, {"running"}) == "running"
        _await_committed(runner_dir, chunk_id, landed_file)
        before = _leases_for_chunk(runner_dir, chunk_id)
        assert len(before) == 1, f"expected one lease before the pause, got {before}"
        lease_id, epoch, session_id, pid_before = before[0]
        assert session_id and pid_before is not None

        # The operator pauses the running chunk — a claim-keeping brake, not a detach.
        paused = hub.post(f"/api/chunks/{chunk_id}/pause", json={"by": "crash-sweep"})
        assert paused.status_code == 202, paused.text

        # The armed runner's next PULL kills the hung worker and self-SIGKILLs before the park.
        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")
        assert _open_pause_parks(runner_dir) == set(), "the park was durable — the crash point fired too late"
        assert _session_ends(runner_dir) == set(), "a SIGKILL'd worker must record no session-end"

        # Restart UNARMED: recovery must re-run the park, NOT abandon the chunk.
        runner_proc = start_runner(runner_dir, crash_point=None)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and _open_pause_parks(runner_dir) != {lease_id}:
            time.sleep(0.25)
        assert _open_pause_parks(runner_dir) == {lease_id}, (
            "the paused lease was never re-parked after the crash — recovery abandoned the chunk "
            "instead of keeping the claim (the issue #46 RESUME fix regressed?)"
        )
        # The claim survived the crash: no closure at all, and emphatically not `released`.
        assert _closure_reason(runner_dir, lease_id) is None, "recovery closed the paused lease — pause became detach"
        assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "paused"
        assert _open_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"
        _assert_invariants(runner_dir, hub_dir, when=f"after the pause-park recovered past {point}")

        # The operator resumes: the SAME session finishes the work it was paused mid-way through.
        resumed = hub.post(f"/api/chunks/{chunk_id}/resume", json={"by": "crash-sweep"})
        assert resumed.status_code == 202, resumed.text
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"

        after = _leases_for_chunk(runner_dir, chunk_id)
        # Nothing worked twice: still exactly one lease, same lease/epoch/session — the pause cost
        # the chunk a process, not an attempt (a retry would have minted a second lease).
        assert len(after) == 1, f"the pause/resume cycle minted an extra lease (retry, not resume): {after}"
        assert (after[0][0], after[0][1], after[0][2]) == (lease_id, epoch, session_id)
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --------------------------------------------------------------------------- #
# Generic hub command node (#65) — the hubnode.* per-step crash windows
# --------------------------------------------------------------------------- #


# The land step's command: open a PR for each submitted branch and merge it by pinned SHA
# against the mock forge — the generic ``run:`` policy #65 makes graph content, driven
# entirely off the injected env, never a typed forge seam (policy-in-YAML). Idempotent by
# construction: re-merging an already-merged head is a git "Already up to date" no-op, so
# the ``hubnode.after-step.before-marker`` re-run lands nothing twice. It prints a
# non-choice line so the executor's outcome mapping falls through to the next step (and,
# after the last step, to the default ``success`` edge) rather than short-circuiting.
# Shared with the generic sweep graph's own ``deliver`` step (:data:`support.LAND_STEP`).
_LAND_STEP = LAND_STEP

# The verify step: a no-op second step whose only job is to be an UNMARKED step past the
# marked land step, so ``hubnode.after-marker.before-next`` has a "next" step to skip the
# land in favour of. It prints a non-choice line, so the run ends on the default success.
_VERIFY_STEP = """python3 - <<'PYEOF'
print("post-land verification ran")
PYEOF
"""


def _hub_command_graph_yaml(landed_file: str) -> str:
    """A ``build -> merge(run:) -> done`` graph whose ``merge`` is a generic hub command node.

    ``build`` commits ``landed_file`` (the runner pushes its branch to the origin on ADVANCE,
    exactly as for the coordinator path); ``merge`` is an ``executor: hub`` node with a
    two-step ``run:`` list — ``land`` (``produces: merged``) opens+merges a PR by pinned SHA,
    ``verify`` (``produces: verified``) is a no-op post-land step. Its judgement authors the
    reserved ``success``/``failure`` choices (#65's fused outcome vocabulary); a clean run
    ends on ``success -> done``, ``done`` being the only terminal (#63)."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build_script(landed_file),
                "judgement": {
                    "prompt": "verdict('pass', 'the mock harness committed the change; checks are green')\n",
                    "choices": {"pass": {"description": "Committed and green.", "to": "merge"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "merge": {
                "executor": "hub",
                "run": [
                    {"name": "land", "command": _LAND_STEP, "produces": "merged"},
                    {"name": "verify", "command": _VERIFY_STEP, "produces": "verified"},
                ],
                "judgement": {
                    "choices": {
                        "success": {"description": "Landed cleanly; finish.", "to": "done"},
                        "failure": {"description": "A step failed; back to build.", "to": "build"},
                    },
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_hub_command_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str) -> str:
    """Mint the hub-command graph, file a fresh issue, and ingest it to a ready chunk."""
    minted = hub.post("/api/graphs", json={"definition_yaml": _hub_command_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a hub-command crash-sweep chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


def _live_exec_slots(hub_dir: Path) -> int:
    """The number of un-released ``hub_exec_slot`` rows — the ``hub:one-live-exec-slot``
    invariant's own quantity, read straight off the hub store so a leaked slot is caught
    directly, not only through the aggregate invariant checker."""
    engine = create_engine_from_url(HubConfig.load(hub_dir).db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(hub_schema.hub_exec_slot.c.slot_id).where(hub_schema.hub_exec_slot.c.released_at.is_(None))
            ).all()
        return len(rows)
    finally:
        engine.dispose()


def _count_pulls(forge: httpx.Client) -> int:
    """Every PR the mock forge holds for the sweep repo, any state (the origins are
    session-shared, so callers compare a before/after delta, never the absolute count)."""
    resp = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"})
    resp.raise_for_status()
    return len(resp.json())


@pytest.mark.parametrize("point", _HUBNODE_SWEEP)
def test_kill9_at_hub_command_node_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` inside a generic hub command node's per-step window recovers, lands once (#65).

    The dedicated hub-command scenario the generic ``build -> deliver`` sweep cannot reach: a
    ``build -> merge(run:) -> done`` graph whose ``merge`` node runs a two-step ``run:`` list —
    ``land`` (``produces: merged``) then ``verify`` (``produces: verified``) — synchronously on
    the build completion, serialized by the fleet-wide ``hub_exec_slot`` FACT. The hub self-
    SIGKILLs inside one of the two per-step windows, and a restart re-drives the executor off the
    re-flushed build completion (its idempotent replay re-enters the hub-node branch).

    The claim under test is the **at-least-once-per-step** contract:

    * ``hubnode.after-step.before-marker`` — the crash is *after* ``land`` ran (its PR is merged)
      but *before* its ``merged`` marker is durable, so recovery **re-runs** ``land`` (a second PR,
      whose merge of an already-merged head is a git no-op). The branch lands on bare ``main``
      **exactly once** despite the step running twice — re-running a step is safe.
    * ``hubnode.after-marker.before-next`` — the crash is *after* ``land``'s marker is durable but
      *before* ``verify`` starts, so recovery **skips** the marked ``land`` (no second PR) and runs
      only ``verify``. The marker is what makes the skip exact.

    Either way the invariant checker is green immediately after the crash and after convergence,
    the ``hub:one-live-exec-slot`` slot is released (no leak), and the file lands exactly once."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # A hubnode.* point fires inside the hub's synchronous executor — arm the hub.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_hub_command_chunk(hub, crash_env.forge, landed_file)
        pulls_before = _count_pulls(crash_env.forge)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # The hub self-SIGKILLs the instant it reaches the armed per-step window inside the
        # merge node's run: list — after land ran, at either the pre-marker or post-marker edge.
        code = wait_death(hub_proc)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")

        # Restart the hub UNARMED: the runner re-flushes the build completion, whose idempotent
        # replay re-enters the hub-node branch and resumes the interrupted run to completion.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")

        # The serialization slot is released — no leaked live slot after the crash-and-resume
        # (the ``hub:one-live-exec-slot`` invariant, asserted directly off the store).
        assert _live_exec_slots(hub_dir) == 0, f"a hub_exec_slot leaked live after convergence past {point}"

        # Exactly-once delivery: the file is reachable from bare main exactly once, no matter how
        # many times the land step ran.
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [ln for ln in tree.splitlines() if ln.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"

        # The per-step contract, made observable through the forge: the land step opens one PR per
        # run, so the number of PRs this chunk created is exactly how many times land ran.
        lands = _count_pulls(crash_env.forge) - pulls_before
        if point == "hubnode.after-step.before-marker":
            assert lands == 2, f"land ran {lands}x — the pre-marker crash must re-run the just-run step"
        elif point == "hubnode.after-marker.before-next":
            assert lands == 1, f"land ran {lands}x — the post-marker crash must skip the marked step"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --------------------------------------------------------------------------- #
# Pending hub command node (#66) — the hubnode.after-poll.* between-polls window
# --------------------------------------------------------------------------- #


# The poll-then-land step: a single ``run:`` step that reports the reserved ``pending``
# outcome on its FIRST poll and lands on every subsequent one. It switches on a durable
# workdir sentinel — the per-chunk hub workdir lives under the hub runtime dir, keyed by
# chunk id, so a file written there survives the hub restart the crash forces (losing it
# would only cost a re-poll, never correctness). The land body is the same policy-in-YAML
# merge-by-pinned-SHA as ``_LAND_STEP``, gated behind the sentinel; it prints a non-choice
# line so a clean land falls through to the default ``success`` edge. The pending branch
# prints the reserved ``pending`` literal and exits 0 — no marker, no transition — which is
# what parks the chunk and releases the fleet-wide slot (#66).
_POLL_THEN_LAND_STEP = """python3 - <<'PYEOF'
import json, os, pathlib, urllib.error, urllib.request

sentinel = pathlib.Path(os.environ["BZ_HUB_WORKDIR"]) / "pending-once.marker"
if not sentinel.exists():
    sentinel.write_text("polled once\\n")
    print("pending")
    raise SystemExit(0)

forge = os.environ["BZ_FORGE_URL"]
base = os.environ.get("BZ_HUB_BASE_BRANCH", "main")
commits = json.loads(os.environ.get("BZ_HUB_GIT_COMMITS") or "[]")


def call(method, path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        forge + path, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, None


for c in commits:
    repo = c["repo"] if "/" in c["repo"] else "blizzard/" + c["repo"]
    status, body = call(
        "POST",
        "/repos/%s/pulls" % repo,
        {"title": "land", "head": c["branch"], "base": base, "body": "", "user": "blizzard-hub"},
    )
    if status == 201 and body:
        call(
            "PUT",
            "/repos/%s/pulls/%s/merge" % (repo, body["number"]),
            {"commit_message": "blizzard: land", "sha": c["commit"], "merge_method": "merge", "user": "blizzard-hub"},
        )
print("landed the submitted branches")
PYEOF
"""


def _pending_graph_yaml(landed_file: str) -> str:
    """A ``build -> merge(run:) -> done`` graph whose ``merge`` hub node polls then lands (#66).

    ``build`` commits ``landed_file`` (the runner pushes its branch on ADVANCE); ``merge``
    is an ``executor: hub`` node with a single ``run:`` step that reports ``pending`` on its
    first poll and lands on the next. A brisk ``poll_interval`` (1s) keeps the between-polls
    gap short so the scenario converges in seconds; a generous ``poll_timeout`` (600s) keeps
    the poll from ever timing out into #64's kick-back — this scenario proves the *resume*
    path, not the timeout path. A clean land ends on the reserved ``success -> done`` edge,
    ``done`` being the only terminal (#63)."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build_script(landed_file),
                "judgement": {
                    "prompt": "verdict('pass', 'the mock harness committed the change; checks are green')\n",
                    "choices": {"pass": {"description": "Committed and green.", "to": "merge"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "merge": {
                "executor": "hub",
                "poll_interval": 1,
                "poll_timeout": 600,
                "run": [{"name": "poll-then-land", "command": _POLL_THEN_LAND_STEP}],
                "judgement": {
                    "choices": {
                        "success": {"description": "Landed cleanly; finish.", "to": "done"},
                        "failure": {"description": "A step failed; back to build.", "to": "build"},
                    },
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_pending_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str) -> str:
    """Mint the poll-then-land graph, file a fresh issue, and ingest it to a ready chunk."""
    minted = hub.post("/api/graphs", json={"definition_yaml": _pending_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a pending-poll crash-sweep chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


@pytest.mark.parametrize("point", _HUBNODE_PENDING_SWEEP)
def test_kill9_at_hub_node_pending_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` in a hub node's between-polls window resumes polling and lands once (#66).

    The dedicated pending scenario the generic hub-command sweep cannot reach (its steps
    always succeed): a ``build -> merge(run:) -> done`` graph whose ``merge`` node reports
    ``pending`` on its first poll — recording a durable ``hub_node_poll`` FACT, no
    transition — then lands on the next. The hub self-SIGKILLs at
    ``hubnode.after-poll.before-slot-release``: the poll fact is durable, but the fleet-wide
    slot's release (in :meth:`HubNodeExecutor.run`'s ``finally``) never ran, so the slot is
    left LIVE.

    The claim under test is that recovery is "keep polling", not a special recovery path,
    because pending-ness is DERIVED from the durable poll fact
    (:func:`~blizzard.hub.domain.work.hub_node_pending`), never held in memory:

    * The invariant checker is green the instant after the crash — one leaked live slot is
      still ``<= 1``, the ``hub:one-live-exec-slot`` bound.
    * A restart re-drives the merge node off the runner's ADVANCE poll. The SAME chunk
      reentrantly reclaims its own still-live slot on its next due poll (no wait on the
      staleness TTL, which only matters for a *different* chunk), the poll-then-land step
      finds its durable workdir sentinel and lands, and the run's ``finally`` releases the
      slot — so no slot is leaked live after convergence.
    * The chunk reaches ``done`` and the file lands on bare ``main`` exactly once, despite
      the node's ``run:`` step running across the crash.

    Pending itself consumed no retry and no bounce (asserted at the component tier); here the
    crash-tier claim is the resume-and-land-once + no-leaked-slot invariant.
    """
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # A hubnode.* point fires inside the hub's synchronous executor — arm the hub.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_pending_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # The hub self-SIGKILLs the instant it reaches the between-polls window: the merge
        # node reported pending on its first poll (poll fact durable), the slot release has
        # not yet run.
        code = wait_death(hub_proc)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")

        # Restart the hub UNARMED: the runner's ADVANCE keeps polling hub-advance; pending-ness
        # is derived from the durable poll fact, so the same chunk resumes polling, reentrantly
        # reclaims its own still-live slot on its next due poll, and lands.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")

        # The serialization slot is released — no leaked live slot after the crash-and-resume
        # (the ``hub:one-live-exec-slot`` invariant, asserted directly off the store).
        assert _live_exec_slots(hub_dir) == 0, f"a hub_exec_slot leaked live after convergence past {point}"

        # Exactly-once delivery: the file is reachable from bare main exactly once, despite the
        # merge node's run: step running across the crash and being re-polled to landing.
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [ln for ln in tree.splitlines() if ln.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --------------------------------------------------------------------------- #
# Mid-script inter-repo-push crash (#67) — the packaged default graph's own window
# --------------------------------------------------------------------------- #
#
# The window the per-step `hubnode.*` registry points cannot reach (see the closed gap
# below): the packaged default graph's `deliver` node runs the real `land_default.py`
# across an arbitrary, chunk-dynamic number of repos INSIDE ONE `run:` step, recording
# each `merged/<repo>` marker through the mid-run callback rather than the executor's
# static per-step `produces:`. Its "kill between two repos' pushes" boundary is therefore
# a WALL-CLOCK race an external `kill -9` of the hub daemon (and the land subprocess it
# spawned) must land inside — not a named in-process crash point the registry can arm.
#
# This scenario mints a 2-repo chunk against the REAL `land_default.py`, arms the script's
# test-only pause (`BZ_HUB_LAND_TEST_PAUSE_SECONDS`) so it stalls right after the FIRST
# repo's marker is durable, and `kill -9`s the whole hub process group (hub + land script)
# inside that pause. Recovery re-drives the executor off the re-flushed build completion;
# `land_default` re-runs, skips the marked repo (`merged/<repo>` already in
# `BZ_HUB_ARTIFACT_NAMES`), and pushes only the still-unmarked one. Both repos land exactly
# once, the `hub:one-live-exec-slot` invariant holds with no leaked slot, and each repo
# carries exactly one PR — proof the marked repo was NOT re-merged.

_WEB_REPO_NAME = "toy-web"
_LAND_STEP_COMMAND = "python3 -m blizzard.hub.graphs.scripts.land_default"


def _two_repo_build_script(landed_file: str) -> str:
    """A build node that commits ``landed_file`` in BOTH fixture repos' worktrees.

    The runner's ADVANCE discovers a produced commit per repo and pushes each, so the
    chunk submits a ``git_commit`` pointer for ``toy-api`` AND ``toy-web`` — a genuine
    2-repo land for the default graph's ``land_default`` script to loop over."""
    return (
        "import subprocess, pathlib\n"
        f"for repo in [{REPO_NAME!r}, {_WEB_REPO_NAME!r}]:\n"
        f"    (pathlib.Path(repo) / {landed_file!r}).write_text('landed by the mid-script sweep\\n')\n"
        '    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
        "    subprocess.run(\n"
        '        ["git", "-C", repo,\n'
        '         "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
        '         "commit", "-m", "feat: land a change in " + repo],\n'
        "        check=True,\n"
        "    )\n"
    )


def _default_graph_two_repo_yaml(landed_file: str) -> str:
    """A ``build -> deliver`` graph named ``default-delivery`` whose ``deliver`` node is
    the REAL packaged ``land_default`` script (not the sweep's ``true`` stand-in).

    The build commits in both repos; ``deliver`` runs ``land_default.py``, which opens +
    merges a PR per repo by pinned SHA and records each ``merged/<repo>`` marker via the
    mid-run callback. ``landed -> done`` / ``conflict -> build`` mirror the packaged
    ``default.yaml``."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _two_repo_build_script(landed_file),
                "judgement": {
                    "prompt": "verdict('pass', 'committed the change in both repos; checks are green')\n",
                    "choices": {"pass": {"description": "Committed and green.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"name": "land-every-repo", "command": _LAND_STEP_COMMAND}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo did not merge; back to build.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


_LAND_FF_STEP_COMMAND = "python3 -m blizzard.hub.graphs.scripts.land_ff"


def _ff_graph_two_repo_yaml(landed_file: str) -> str:
    """:func:`_default_graph_two_repo_yaml`'s twin for the PR-free lane: a ``build ->
    deliver`` graph named ``default-delivery`` whose ``deliver`` node is the REAL
    packaged ``land_ff`` script (the same one ``basic-development-workflow/graph.yaml``
    wires its own ``deliver`` node to), not ``land_default``.

    The build commits in both repos; ``deliver`` runs ``land_ff.py``, which fast-forwards
    each repo's base branch ref directly to the chunk's own commit (no PR, no merge
    commit) and records each ``merged/<repo>`` marker via the mid-run callback.
    ``landed -> done`` / ``conflict -> build`` mirror the packaged graph's own edges."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _two_repo_build_script(landed_file),
                "judgement": {
                    "prompt": "verdict('pass', 'committed the change in both repos; checks are green')\n",
                    "choices": {"pass": {"description": "Committed and green.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"name": "land-every-repo", "command": _LAND_FF_STEP_COMMAND}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo fast-forwarded cleanly.", "to": "done"},
                        "conflict": {"description": "A repo did not fast-forward; back to build.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _merged_markers(hub: httpx.Client, chunk_id: str) -> list[str]:
    """The chunk's durable ``merged/<repo>`` marker artifact names, read through the hub API."""
    detail = hub.get(f"/api/chunks/{chunk_id}")
    detail.raise_for_status()
    return sorted(a["name"] for a in detail.json()["artifacts"] if a["name"].startswith("merged/"))


def _repo_pull_count(forge: httpx.Client, repo: str) -> int:
    resp = forge.get(f"/repos/{OWNER}/{repo}/pulls", params={"state": "all"})
    resp.raise_for_status()
    return len(resp.json())


def test_kill9_between_default_graph_repo_pushes(crash_env: CrashEnv, tmp_path: Path) -> None:
    """A ``kill -9`` between two repos' pushes in the real ``land_default`` re-merges only the
    unmarked repo, landing each exactly once (#67 — the verify finale's closed gap).

    The packaged default graph's own mid-script window, unreachable by the per-step
    ``hubnode.*`` registry points: ``land_default.py`` loops over both fixture repos inside
    ONE ``run:`` step. Armed with its test-only pause, it stalls right after the FIRST repo's
    ``merged/<repo>`` marker is durable; the whole hub process group (the hub daemon plus the
    land subprocess it spawned) is ``kill -9``ed inside that pause — a faithful reboot mid-land.

    The claim: recovery re-drives the executor off the re-flushed build completion, and the
    re-run of ``land_default`` skips the already-marked repo and pushes only the unmarked one.

    * the invariant checker is green the instant after the crash and again after convergence;
    * exactly one ``merged/<repo>`` marker was durable at crash time (one repo landed, one not);
    * both repos' change lands on their bare ``main`` **exactly once**, and each repo carries
      exactly ONE PR — the marked repo was not re-merged;
    * the ``hub:one-live-exec-slot`` slot is released after convergence — no leaked live slot.
    """
    landed_file = "LANDED-mid-script-sweep.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    api_bare = crash_env.origins / f"{REPO_NAME}.git"
    web_bare = crash_env.origins / f"{_WEB_REPO_NAME}.git"

    # The hub is a session/group leader (new_session) so its whole tree can be killpg'd,
    # and carries the land script's test-only pause so the between-repos window is wide.
    hub_proc = start_hub(
        hub_dir,
        forge_port=crash_env.forge_port,
        port=hub_port,
        crash_point=None,
        new_session=True,
        extra_env={"BZ_HUB_LAND_TEST_PAUSE_SECONDS": "30"},
    )
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        api_pulls_before = _repo_pull_count(crash_env.forge, REPO_NAME)
        web_pulls_before = _repo_pull_count(crash_env.forge, _WEB_REPO_NAME)

        minted = hub.post("/api/graphs", json={"definition_yaml": _default_graph_two_repo_yaml(landed_file)})
        assert minted.status_code == 201, minted.text
        issue = crash_env.forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a mid-script chunk"})
        assert issue.status_code == 201, issue.text
        number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # Wait until exactly ONE repo's marker is durable — the land script is now paused,
        # inside the between-repos window, with the second repo not yet merged.
        deadline = time.monotonic() + 90.0
        markers: list[str] = []
        while time.monotonic() < deadline:
            markers = _merged_markers(hub, chunk_id)
            if len(markers) == 1:
                break
            assert len(markers) < 2, f"both markers landed before the kill — pause too short? ({markers})"
            time.sleep(0.25)
        assert len(markers) == 1, f"the land script never reached its one-marker pause window (saw {markers})"

        # kill -9 the WHOLE hub tree (daemon + the paused land subprocess) mid-script.
        os.killpg(os.getpgid(hub_proc.pid), signal.SIGKILL)
        assert wait_death(hub_proc) == -9

        # Invariant checker green right after the crash — one marker durable, one repo unlanded.
        _assert_invariants(runner_dir, hub_dir, when="immediately after mid-script kill -9")

        # Restart the hub UNARMED (no pause env): the runner re-flushes the build completion,
        # land_default re-runs, skips the marked repo, and pushes only the unmarked one.
        hub_proc = start_hub(
            hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, new_session=True
        )
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"}, timeout=120.0)
        assert status == "done", f"chunk did not converge to done after the mid-script kill (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when="after convergence past the mid-script kill")

        # Both markers are now durable, and no live exec slot leaked.
        assert _merged_markers(hub, chunk_id) == sorted([f"merged/{REPO_NAME}", f"merged/{_WEB_REPO_NAME}"])
        assert _live_exec_slots(hub_dir) == 0, "a hub_exec_slot leaked live after the mid-script recovery"

        # Exactly-once: each repo's change is reachable from its bare main exactly once.
        for bare in (api_bare, web_bare):
            tree = git_bare(bare, "log", "--oneline", "--", landed_file)
            landings = [ln for ln in tree.splitlines() if ln.strip()]
            assert len(landings) == 1, f"{landed_file} landed {len(landings)}x on {bare.name}:\n{tree}"

        # The marked repo was NOT re-merged: each repo created exactly one PR across the whole run.
        assert _repo_pull_count(crash_env.forge, REPO_NAME) - api_pulls_before == 1, "toy-api opened != 1 PR"
        assert _repo_pull_count(crash_env.forge, _WEB_REPO_NAME) - web_pulls_before == 1, "toy-web opened != 1 PR"
    finally:
        hub.close()
        terminate(runner_proc)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(hub_proc.pid), signal.SIGKILL)
        terminate(hub_proc)


# --------------------------------------------------------------------------- #
# Mid-script inter-repo-update crash for the PR-free lane — `land_ff`'s own window
# --------------------------------------------------------------------------- #
#
# `land_ff.py`'s mirror of the closed #67 gap above: its own `deliver` node runs the
# real `land_ff.py` across an arbitrary, chunk-dynamic number of repos INSIDE ONE
# `run:` step, recording each `merged/<repo>` marker through the mid-run callback
# rather than the executor's static per-step `produces:`. Repos are updated ONE AT A
# TIME (no PR to check first, unlike `land_default` — see the module docstring), so its
# "kill between two repos' fast-forwards" boundary is the same kind of WALL-CLOCK race
# an external `kill -9` of the hub daemon (and the land subprocess it spawned) must
# land inside — not a named in-process crash point the registry can arm.
#
# This scenario mints a 2-repo chunk against the REAL `land_ff.py`, arms the script's
# own test-only pause (`BZ_HUB_LAND_TEST_PAUSE_SECONDS`, the same env var and guard
# shape as `land_default`'s) so it stalls right after the FIRST repo's marker is
# durable, and `kill -9`s the whole hub process group (hub + land script) inside that
# pause. Recovery re-drives the executor off the re-flushed build completion;
# `land_ff` re-runs, skips the marked repo (`merged/<repo>` already in
# `BZ_HUB_ARTIFACT_NAMES`), and fast-forwards only the still-unmarked one. Both repos
# land exactly once and the `hub:one-live-exec-slot` invariant holds with no leaked
# slot — no PR count to check (this lane opens none), so exactly-once is proven
# directly against each bare repo's own history.


def test_kill9_between_ff_graph_repo_pushes(crash_env: CrashEnv, tmp_path: Path) -> None:
    """A ``kill -9`` between two repos' fast-forwards in the real ``land_ff`` re-runs only the
    unmarked repo, landing each exactly once — ``land_ff``'s own mid-script window, the PR-free
    lane's mirror of ``test_kill9_between_default_graph_repo_pushes`` (#67, #123).

    The packaged fast-forward graph's own mid-script window, unreachable by the per-step
    ``hubnode.*`` registry points: ``land_ff.py`` loops over both fixture repos inside ONE
    ``run:`` step. Armed with its test-only pause, it stalls right after the FIRST repo's
    ``merged/<repo>`` marker is durable; the whole hub process group (the hub daemon plus the
    land subprocess it spawned) is ``kill -9``ed inside that pause — a faithful reboot mid-land.

    The claim: recovery re-drives the executor off the re-flushed build completion, and the
    re-run of ``land_ff`` skips the already-marked repo and fast-forwards only the unmarked one.

    * the invariant checker is green the instant after the crash and again after convergence;
    * exactly one ``merged/<repo>`` marker was durable at crash time (one repo landed, one not);
    * both repos' change lands on their bare ``main`` **exactly once** (no PR to double-check —
      the lane opens none — so exactly-once is read straight off each bare repo's history);
    * the ``hub:one-live-exec-slot`` slot is released after convergence — no leaked live slot.
    """
    landed_file = "LANDED-mid-script-ff-sweep.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    api_bare = crash_env.origins / f"{REPO_NAME}.git"
    web_bare = crash_env.origins / f"{_WEB_REPO_NAME}.git"

    # The hub is a session/group leader (new_session) so its whole tree can be killpg'd,
    # and carries the land script's test-only pause so the between-repos window is wide.
    hub_proc = start_hub(
        hub_dir,
        forge_port=crash_env.forge_port,
        port=hub_port,
        crash_point=None,
        new_session=True,
        extra_env={"BZ_HUB_LAND_TEST_PAUSE_SECONDS": "30"},
    )
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)

        minted = hub.post("/api/graphs", json={"definition_yaml": _ff_graph_two_repo_yaml(landed_file)})
        assert minted.status_code == 201, minted.text
        issue = crash_env.forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a mid-script chunk"})
        assert issue.status_code == 201, issue.text
        number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # Wait until exactly ONE repo's marker is durable — the land script is now paused,
        # inside the between-repos window, with the second repo not yet fast-forwarded.
        deadline = time.monotonic() + 90.0
        markers: list[str] = []
        while time.monotonic() < deadline:
            markers = _merged_markers(hub, chunk_id)
            if len(markers) == 1:
                break
            assert len(markers) < 2, f"both markers landed before the kill — pause too short? ({markers})"
            time.sleep(0.25)
        assert len(markers) == 1, f"the land script never reached its one-marker pause window (saw {markers})"

        # kill -9 the WHOLE hub tree (daemon + the paused land subprocess) mid-script.
        os.killpg(os.getpgid(hub_proc.pid), signal.SIGKILL)
        assert wait_death(hub_proc) == -9

        # Invariant checker green right after the crash — one marker durable, one repo unlanded.
        _assert_invariants(runner_dir, hub_dir, when="immediately after mid-script kill -9 (land_ff)")

        # Restart the hub UNARMED (no pause env): the runner re-flushes the build completion,
        # land_ff re-runs, skips the marked repo, and fast-forwards only the unmarked one.
        hub_proc = start_hub(
            hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, new_session=True
        )
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"}, timeout=120.0)
        assert status == "done", f"chunk did not converge to done after the mid-script kill (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when="after convergence past the mid-script kill (land_ff)")

        # Both markers are now durable, and no live exec slot leaked.
        assert _merged_markers(hub, chunk_id) == sorted([f"merged/{REPO_NAME}", f"merged/{_WEB_REPO_NAME}"])
        assert _live_exec_slots(hub_dir) == 0, "a hub_exec_slot leaked live after the mid-script recovery (land_ff)"

        # Exactly-once: each repo's change is reachable from its bare main exactly once — no
        # PR to double-check in this lane, so this is the whole exactly-once proof.
        for bare in (api_bare, web_bare):
            tree = git_bare(bare, "log", "--oneline", "--", landed_file)
            landings = [ln for ln in tree.splitlines() if ln.strip()]
            assert len(landings) == 1, f"{landed_file} landed {len(landings)}x on {bare.name}:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(hub_proc.pid), signal.SIGKILL)
        terminate(hub_proc)
