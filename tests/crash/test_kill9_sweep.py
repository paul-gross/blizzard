"""The kill-9 sweep (``blizzard:crash-sweep``) — MVP acceptance criterion 4.

For each crash point in the registry (``bzh:crash-point-registry``): arms it so the
owning daemon SIGKILLs itself there, restarts unarmed, and asserts invariants stay
green and the chunk lands exactly once. Families the generic scenario can't reach get
a dedicated scenario each. Gated by ``BLIZZARD_CRASH_SWEEP=1``; see ``conftest.py``."""

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
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.enrollment import TokenHash
from blizzard.hub.store import schema as hub_schema
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.internal.winter_cli import SubprocessWinterCli
from blizzard.runner.store import schema as runner_schema
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import NewLease
from blizzard.tools.invariants import Invariants
from tests.crash.support import (
    LAND_STEP,
    OWNER,
    REPO,
    REPO_NAME,
    RUNNER_ENV,
    CrashEnv,
    await_http,
    build_script,
    checks_graph_yaml,
    free_port,
    git_bare,
    graph_yaml,
    intended_migrate_source_yaml,
    migrate_hub_source_yaml,
    migrate_hub_target_yaml,
    migrate_source_yaml,
    migrate_target_yaml,
    nudge_graph_yaml,
    pre_declare_build_script,
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

# RESUME's and abandon's crash points fire only on conditions the generic scenario below
# never creates; each dedicated scenario named in ``_DEDICATED_PREFIXES`` sweeps its own.
_DEDICATED_PREFIXES = (
    "resume.",
    "abandon.",
    "pause.",
    "hubnode.",
    "migrate.",
    "attach.",
    "nudge.",
    "declare-commit.",
    "checks.",
    "preempt.",
    "close.",
)
_RESUME_POINTS = [p for p in _ALL_POINTS if p.startswith("resume.")]
_ABANDON_POINTS = [p for p in _ALL_POINTS if p.startswith("abandon.")]
_PAUSE_POINTS = [p for p in _ALL_POINTS if p.startswith("pause.")]
# `hubnode.*` (#65) and its narrower `hubnode.after-poll.` carve-out (#66) fire inside the
# hub's synchronous executor; each is swept by its own dedicated test below.
_HUBNODE_PENDING_POINTS = [p for p in _ALL_POINTS if p.startswith("hubnode.after-poll.")]
_HUBNODE_POINTS = [p for p in _ALL_POINTS if p.startswith("hubnode.") and p not in _HUBNODE_PENDING_POINTS]
# `migrate.*` fires inside the HUB on a cross-graph judgement choice (issue #90). Swept by
# `test_kill9_at_migrate_crash_point`.
_MIGRATE_POINTS = [p for p in _ALL_POINTS if p.startswith("migrate.")]
# `attach.*` fires on the RUNNER's out-of-band attach endpoint (issue #113). Swept by
# `test_kill9_at_attach_crash_point`.
_ATTACH_POINTS = [p for p in _ALL_POINTS if p.startswith("attach.")]
# `declare-commit.*` fires on the RUNNER's out-of-band declare endpoint, `attach.*`'s
# sibling (issue #143). Swept by `test_kill9_at_declare_commit_crash_point`.
_DECLARE_COMMIT_POINTS = [p for p in _ALL_POINTS if p.startswith("declare-commit.")]
# `nudge.*` fires in the RUNNER's ADVANCE step for an unattached `produces:` name (issue
# #113 Phase 4). Swept by `test_kill9_at_nudge_crash_point`.
_NUDGE_POINTS = [p for p in _ALL_POINTS if p.startswith("nudge.")]
# `checks.*` fires in the RUNNER's ADVANCE step for a node's `checks:` command (#114).
# Swept by `test_kill9_at_checks_crash_point`.
_CHECKS_POINTS = [p for p in _ALL_POINTS if p.startswith("checks.")]
# `preempt.*` fires in the RUNNER's PULL step, inside the teardown an operator restart forces
# (#370). Swept by `test_kill9_at_preempt_crash_point`.
_PREEMPT_POINTS = [p for p in _ALL_POINTS if p.startswith("preempt.")]
# `close.*` fires inside the HUB — the close-intent outbox's own enqueue-then-drain
# windows (blizzard#383). Swept by `test_kill9_at_close_crash_point`.
_CLOSE_POINTS = [p for p in _ALL_POINTS if p.startswith("close.")]
_GENERIC_POINTS = [p for p in _ALL_POINTS if not p.startswith(_DEDICATED_PREFIXES)]

# A representative CI subset, one point per family, run as a bounded-runtime gate under
# `BLIZZARD_CRASH_SWEEP_CI=1`; the full sweep is the local/`release`-workflow command.
_CI_SUBSET = (
    "reap.after-expire",
    "pull.after-flush",
    "fill.after-bind.before-claim",
    "spawn.after-lease-mint.before-spawn",
    "advance.after-buffer.before-flush",
    "flush.after-submit.before-ack",
    # `claim.*` (issue #84b) is a boundary family within `_GENERIC_POINTS`; a family's lone
    # member is its own CI representative.
    "claim.after-persist.before-response",
    # `transcript.*` (D3, issue #246): reachable here with no dedicated scenario — every
    # lease closure enqueues a final marker regardless of `[transcripts] ship`.
    "transcript.after-submit.before-ack",
)

# The resume CI subset: the recovery-critical kill-first window, bounding wall time
# (each resume case restarts the runner twice).
_RESUME_CI_SUBSET = ("resume.after-kill.before-reattach",)

# The abandon CI subset, bounding wall time at one case for a two-member family (blizzard#280):
# `after-release.before-closure` is the strictly-later state of this same scenario, so the earlier
# window is the wider arm. The full sweep runs both; only CI drops one.
_ABANDON_CI_SUBSET = ("abandon.after-kill.before-release",)

# The pause CI subset (#46): the family's lone point, the regression fence on the
# issue's central bug.
_PAUSE_CI_SUBSET = ("pause.after-kill.before-park",)

# The hub command node CI subset (#65): the family's first-declared member is its own CI
# representative.
_HUBNODE_CI_SUBSET = ("hubnode.after-step.before-marker",)

# The hub-node pending CI subset (#66): the narrower between-polls window carved out of the
# `hubnode.*` family above; its lone member is its own CI representative.
_HUBNODE_PENDING_CI_SUBSET = ("hubnode.after-poll.before-slot-release",)

# The migrate CI subset (#90): the family's lone member is its own CI representative.
_MIGRATE_CI_SUBSET = ("migrate.after-record.before-response",)

# The attach CI subset (#113): the family's lone member is its own CI representative.
_ATTACH_CI_SUBSET = ("attach.after-record.before-response",)

# The declare-commit CI subset (#143): the family's lone member is its own CI representative.
_DECLARE_COMMIT_CI_SUBSET = ("declare-commit.after-record.before-response",)

# The nudge CI subset (#113, #422): the family's lone member — the fired-fact-before-resume
# window the "at most one resume" guarantee rests on — is its own CI representative.
_NUDGE_CI_SUBSET = ("nudge.after-fired-fact.before-resume",)

# The checks CI subset (#114): the family's recovery-critical member — the results-before-marker
# window — is its own CI representative.
_CHECKS_CI_SUBSET = ("checks.after-results.before-marker",)

# The preempt CI subset (#370): the family's lone member is its own CI representative.
_PREEMPT_CI_SUBSET = ("preempt.after-kill.before-closure",)

# The close CI subset (blizzard#383): the recovery-critical member — the drain's own
# after-close, before-record window — is its own CI representative.
_CLOSE_CI_SUBSET = ("close.after-close.before-record",)


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
_CHECKS_SWEEP = _select(_CHECKS_POINTS, _CHECKS_CI_SUBSET)
_DECLARE_COMMIT_SWEEP = _select(_DECLARE_COMMIT_POINTS, _DECLARE_COMMIT_CI_SUBSET)
_PREEMPT_SWEEP = _select(_PREEMPT_POINTS, _PREEMPT_CI_SUBSET)
_CLOSE_SWEEP = _select(_CLOSE_POINTS, _CLOSE_CI_SUBSET)


def test_ci_subset_covers_every_family(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every family prefix in the registry yields a non-empty CI-profile selection —
    a new registry point never silently drops out of CI coverage."""
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
        | set(_select(_CHECKS_POINTS, _CHECKS_CI_SUBSET))
        | set(_select(_DECLARE_COMMIT_POINTS, _DECLARE_COMMIT_CI_SUBSET))
        | set(_select(_PREEMPT_POINTS, _PREEMPT_CI_SUBSET))
        | set(_select(_CLOSE_POINTS, _CLOSE_CI_SUBSET))
    )
    uncovered = {family for family in families if not any(p.startswith(f"{family}.") for p in ci_selected)}
    assert not uncovered, f"registry families with zero CI-subset coverage: {sorted(uncovered)}"


def _assert_invariants(runner_dir: Path, hub_dir: Path, *, when: str) -> None:
    runner_db = RunnerConfig.load(runner_dir).db_url
    hub_db = HubConfig.load(hub_dir).db_url
    violations = Invariants(runner_db_url=runner_db, hub_db_url=hub_db).run()
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


# `claim.*` fires inside the HUB (issue #84b) — the one `_GENERIC_POINTS` family that
# arms the hub rather than the runner; `test_kill9_at_crash_point` reads this to pick.
_HUB_SIDE_GENERIC_PREFIXES = ("claim.",)


@pytest.mark.parametrize("point", _POINTS)
def test_kill9_at_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` at ``point`` recovers to a correct state and the chunk lands once."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    on_hub = point.startswith(_HUB_SIDE_GENERIC_PREFIXES)

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point if on_hub else None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        # For a `claim.*` point, ingest+promote alone leaves the chunk ready; the runner's
        # own FILL claim below is what drives the hub into the armed window.
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
    """A ``kill -9`` right after a cross-graph migration is recorded still recovers (#90) —
    the migration fact survives the crash and the chunk lands on bare ``main`` exactly
    once, its history carrying exactly one migration."""
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


def _ingest_intended_migrate_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str) -> tuple[str, str]:
    """Mint the (plain, single-graph) source and its migration target, file a fresh
    issue, ingest + promote a chunk pinned to the source, then PATCH a ``forced``
    migration intent onto it — the intent's window is open at ``ready`` (before any
    claim), so this stands the intent up before the runner ever touches the chunk.
    Returns ``(chunk_id, target_graph_id)``."""
    target = hub.post("/api/graphs", json={"definition_yaml": migrate_target_yaml(landed_file)})
    assert target.status_code == 201, target.text
    target_graph_id = target.json()["graph_id"]
    src = hub.post("/api/graphs", json={"definition_yaml": intended_migrate_source_yaml()})
    assert src.status_code == 201, src.text
    issue = forge.post(
        f"/repos/{REPO}/issues", json={"title": landed_file, "body": "an intended-migration crash-sweep chunk"}
    )
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    patched = hub.patch(
        f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": target_graph_id, "node": "build"}}
    )
    assert patched.status_code == 202, patched.text
    return chunk_id, target_graph_id


@pytest.mark.parametrize("point", _MIGRATE_SWEEP)
def test_kill9_at_migrate_crash_point_for_an_intended_migration(
    crash_env: CrashEnv, tmp_path: Path, point: str
) -> None:
    """A ``kill -9`` right after an **intended** migration is recorded still recovers
    (issue #124) — the intent is durably cleared in the same transaction as the migration
    fact, so recovery never re-fires it, and the target's build + deliver lands once."""
    landed_file = f"LANDED-INTENDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # Arm the HUB: the migrate window opens inside its completions handler, not the runner.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id, target_graph_id = _ingest_intended_migrate_chunk(hub, crash_env.forge, landed_file)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # The runner claims, the worker completes `build`, the consult fires the standing
        # intent, and the hub self-SIGKILLs in the window.
        code = wait_death(hub_proc)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after intended-migration kill at {point}")

        # The intent must be cleared in the SAME transaction as the migration fact — a
        # crash that recorded one but not the other would re-fire the migration on recovery.
        hub_engine = create_engine_from_url(HubConfig.load(hub_dir).db_url)
        with hub_engine.connect() as conn:
            migrations = conn.execute(
                select(hub_schema.chunk_migrations).where(hub_schema.chunk_migrations.c.chunk_id == chunk_id)
            ).all()
            intent = conn.execute(
                select(hub_schema.chunks.c.intended_migration).where(hub_schema.chunks.c.chunk_id == chunk_id)
            ).scalar_one()
        assert len(migrations) == 1, "the migration fact was not durably recorded before the crash"
        assert intent is None, "the intent was not durably cleared alongside the migration fact"

        # Restart the hub UNARMED; the runner's replayed completion re-derives MIGRATED and
        # the chunk re-queues + lands under the target graph — the intent never re-fires.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after intended-migration convergence past {point}")

        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert detail["graph_id"] == target_graph_id, "the chunk was not re-pinned to the intent's target graph"
        assert len(detail["migrations"]) == 1, "the two-graph history is missing its migration step — or has two"
        assert detail["intended_migration"] is None, "the intent re-appeared on recovery — it must land only once"

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
    (issue #111) still recovers — the retained route and derived ``delivering`` state
    let the holding runner's ADVANCE poll carry the chunk to ``done`` without wedging."""
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
    """A ``kill -9`` right after the runner records a worker attachment keeps it, with
    full provenance readable against the same store after the ungraceful death (issue
    #113 criterion 3)."""
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
    store.record_lease_token("lease_attach", TokenHash(_ATTACH_TOKEN).hex, _ATTACH_NOW)
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

        # Durable across the kill -9: reopen the same store — the attachment is what the
        # worker submitted, though the 200 never returned.
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
        violations = Invariants(runner_db_url=db_url).run()
        assert not violations, "invariant violations after the attach crash:\n" + "\n".join(str(v) for v in violations)

        # Restart the runner UNARMED; the attachment is still readable against the same store.
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


_DECLARE_COMMIT_TOKEN = "the-declare-commit-lease-token"
# The fixture workspace's one env and its one repo — the declare edge checks the repo
# against that env's real manifest, so these must be the actual ones, not stand-ins.
_DECLARE_COMMIT_ENV = RUNNER_ENV
_DECLARE_COMMIT_REPO = REPO_NAME
_DECLARE_COMMIT_BRANCH = "feat/declare-commit"
_DECLARE_COMMIT_SHA = "deadbeefcafef00d"
_DECLARE_COMMIT_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("point", _DECLARE_COMMIT_SWEEP)
def test_kill9_at_declare_commit_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` right after the runner records a worker git-commit declaration keeps
    it, with full provenance readable against the same store after the ungraceful death
    (issue #143)."""
    runner_dir = tmp_path / "runner"
    # Materialize the env's worktrees directly (this scenario seeds its lease + binding,
    # skipping FILL's acquire), via the same winter-CLI seam the daemon uses.
    winter_cli = SubprocessWinterCli()
    winter_cli.ensure_ready(crash_env.workspace)
    winter_cli.run(crash_env.workspace, ["ws", "init", _DECLARE_COMMIT_ENV])
    # Nothing listens on ``hub_port`` — the declare path never calls the hub; the loop's
    # hub polls just fail and are swallowed, and the local API serves regardless.
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
            lease_id="lease_declare_commit",
            chunk_id="ch_declare_commit",
            graph_id="gr_declare_commit",
            node_id="nd_build",
            node_name="build",
            epoch=4,
            runner_id="runner-local",
            retries_max=2,
            created_at=_DECLARE_COMMIT_NOW,
        )
    )
    store.record_lease_token("lease_declare_commit", TokenHash(_DECLARE_COMMIT_TOKEN).hex, _DECLARE_COMMIT_NOW)
    # The env this declaration resolves against — the declare edge reads the chunk's
    # bindings to decide which environment's manifest to check the repo against.
    store.record_binding(
        chunk_id="ch_declare_commit",
        environment_id=_DECLARE_COMMIT_ENV,
        workdir=str(crash_env.workspace / _DECLARE_COMMIT_ENV),
        bound_at=_DECLARE_COMMIT_NOW,
    )
    store.record_ask(
        lease_id="lease_declare_commit",
        chunk_id="ch_declare_commit",
        question_id="q_park",
        question="parked so REAP leaves the seeded lease be",
        options=[],
        session_id=None,
        asked_at=_DECLARE_COMMIT_NOW,
    )
    store.record_park(
        lease_id="lease_declare_commit",
        chunk_id="ch_declare_commit",
        question_id="q_park",
        parked_at=_DECLARE_COMMIT_NOW,
    )
    engine.dispose()

    runner = httpx.Client(base_url=f"http://127.0.0.1:{runner_port}", timeout=30.0)
    runner_proc = start_runner(runner_dir, crash_point=point)
    try:
        await_http(runner, "/api/health", proc=runner_proc)

        # The runner records the declaration durably, then self-SIGKILLs before the
        # response — the client sees the killed connection, never a 200.
        with pytest.raises(httpx.HTTPError):
            runner.post(
                "/api/leases/lease_declare_commit/git-commits",
                json={
                    "repo": _DECLARE_COMMIT_REPO,
                    "branch": _DECLARE_COMMIT_BRANCH,
                    "commit": _DECLARE_COMMIT_SHA,
                },
                headers={"X-Blizzard-Lease-Token": _DECLARE_COMMIT_TOKEN},
            )
        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"

        # Durable across the kill -9: reopen the same store — the declaration is what
        # the worker submitted, though the 200 never returned.
        engine2 = create_engine_from_url(db_url)
        try:
            declarations = SqlAlchemyRunnerStore(engine2).git_commit_declarations_for_lease("lease_declare_commit")
            assert set(declarations) == {(_DECLARE_COMMIT_ENV, _DECLARE_COMMIT_REPO)}
            declared = declarations[(_DECLARE_COMMIT_ENV, _DECLARE_COMMIT_REPO)]
            assert (declared.environment_id, declared.repo, declared.branch, declared.commit) == (
                _DECLARE_COMMIT_ENV,
                _DECLARE_COMMIT_REPO,
                _DECLARE_COMMIT_BRANCH,
                _DECLARE_COMMIT_SHA,
            )
            with engine2.connect() as conn:
                rows = conn.execute(
                    select(runner_schema.git_commit_declarations).where(
                        runner_schema.git_commit_declarations.c.lease_id == "lease_declare_commit"
                    )
                ).all()
            assert len(rows) == 1, "the declaration was not durably recorded before the crash"
            row = rows[0]._mapping
            assert (row["chunk_id"], row["node_id"], row["epoch"]) == ("ch_declare_commit", "nd_build", 4), (
                "the declaration's provenance did not survive intact"
            )
        finally:
            engine2.dispose()

        # The invariant checker is green over the durable runner facts right after the crash.
        violations = Invariants(runner_db_url=db_url).run()
        assert not violations, "invariant violations after the declare-commit crash:\n" + "\n".join(
            str(v) for v in violations
        )

        # Restart the runner UNARMED; the declaration is still readable against the same store.
        runner_proc = start_runner(runner_dir, crash_point=None)
        await_http(runner, "/api/health", proc=runner_proc)
        engine3 = create_engine_from_url(db_url)
        try:
            declarations = SqlAlchemyRunnerStore(engine3).git_commit_declarations_for_lease("lease_declare_commit")
            assert set(declarations) == {(_DECLARE_COMMIT_ENV, _DECLARE_COMMIT_REPO)}
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
    and the chunk still landing exactly once (issue #113, Phase 4)."""
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


def _ingest_checks_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str) -> str:
    """:func:`_ingest_chunk`'s twin, minting :func:`checks_graph_yaml` — the one green
    ``checks:`` command on ``build`` is what opens the `checks.*` windows this scenario arms."""
    minted = hub.post("/api/graphs", json={"definition_yaml": checks_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a checks crash-sweep chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


@pytest.mark.parametrize("point", _CHECKS_SWEEP)
def test_kill9_at_checks_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` at a `checks.*` window recovers with the chunk still landing exactly
    once and ``runner:checks-recorded-when-marked`` green (issue #114)."""
    landed_file = f"CHECKS-LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_checks_chunk(hub, crash_env.forge, landed_file)

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

        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


def _pre_declare_graph_yaml(landed_file: str, pushed_marker: Path, go_marker: Path) -> str:
    """:func:`graph_yaml`'s ``build -> deliver`` shape, with
    :func:`tests.crash.support.pre_declare_build_script` in place of :func:`build_script` — the
    pre-declaration-window scenario's build node (``bzh:crash-sweep`` D2, case 2)."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": pre_declare_build_script(landed_file, pushed_marker, go_marker),
                "produces": [{"name": "commit", "kind": "git_commit"}],
                "judgement": {
                    "prompt": "verdict('pass', 'the mock harness committed the change; checks are green')\n",
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


def _ingest_pre_declare_chunk(
    hub: httpx.Client, forge: httpx.Client, landed_file: str, pushed_marker: Path, go_marker: Path
) -> str:
    """Mint the pre-declaration-window graph and ingest a fresh issue against it to a ready chunk."""
    minted = hub.post(
        "/api/graphs", json={"definition_yaml": _pre_declare_graph_yaml(landed_file, pushed_marker, go_marker)}
    )
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a pre-declaration-window chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


def test_kill9_runner_daemon_after_session_end(crash_env: CrashEnv, tmp_path: Path) -> None:
    """External ``kill -9`` of the runner daemon strictly AFTER the worker's commit is
    declared and its ``SessionEnd`` is durable — the exit-is-done recovery path
    (``_crash_orphaned``'s ``ended`` skip), pinned deterministically (``bzh:crash-sweep`` D2)."""
    landed_file = "LANDED-runner-after-session-end.md"
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

        assert wait_status(hub, chunk_id, {"running"}) == "running"
        lease_id, epoch, session_id, worker_pid = _lease_for_chunk(runner_dir, chunk_id)

        # Fence: wait for the durable SessionEnd fact — the runner stays alive throughout,
        # so both the declare and the session-end POST land before the kill.
        _wait_session_ended(runner_dir, lease_id)
        # Property 2: the window is provably open at the kill instant, not merely hoped for.
        assert _git_commit_declared(runner_dir, lease_id), "session ended before its commit was declared"
        pre_kill_status = hub.get(f"/api/chunks/{chunk_id}").json()["status"]
        assert pre_kill_status != "done", "the original daemon already judged the chunk before the kill landed"

        pid_before = runner_proc.pid
        runner_proc.kill()
        runner_proc.wait(timeout=10)

        _assert_invariants(runner_dir, hub_dir, when="after external kill -9 following the worker's session-end")

        runner_proc = start_runner(runner_dir, crash_point=None)
        assert runner_proc.pid != pid_before
        assert wait_status(hub, chunk_id, {"done"}) == "done", "chunk did not converge after runner kill -9"
        _assert_invariants(runner_dir, hub_dir, when="after runner-daemon recovery")

        after = _leases_for_chunk(runner_dir, chunk_id)
        assert len(after) == 1, f"exit-is-done recovery minted an extra lease (a retry, not a direct judge): {after}"
        assert after[0][:3] == (lease_id, epoch, session_id)
        # Mutation-sensitive (`bzh:mutation-review-selection`): a restart-resume respawns
        # under a NEW pid; judging an already-declared-done lease directly never does.
        assert after[0][3] == worker_pid, (
            f"the exit-is-done lease was respawned (pid {worker_pid} -> {after[0][3]}) — "
            "the `ended` skip did not fire; it went through restart-resume instead"
        )
        assert _open_resume_intents(runner_dir) == set()

        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


def test_kill9_runner_daemon_before_commit_declared(crash_env: CrashEnv, tmp_path: Path) -> None:
    """External ``kill -9`` of the runner daemon precisely BEFORE the worker declares its
    commit — issue #284's pre-declaration race, pinned deterministically — must never land
    ``done`` with an empty delivery. D1's empty-delivery refusal is what this proves."""
    landed_file = "LANDED-runner-pre-declare.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    pushed_marker = tmp_path / "pushed.marker"
    go_marker = tmp_path / "go.marker"

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_pre_declare_chunk(hub, crash_env.forge, landed_file, pushed_marker, go_marker)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        assert wait_status(hub, chunk_id, {"running"}) == "running"
        lease_id, _epoch, _session_id, worker_pid = _lease_for_chunk(runner_dir, chunk_id)

        # Fence: wait for the push, strictly before the declare — an in-test fence
        # (``bzh:crash-sweep`` D2 property 3), not a race against the worker's pace.
        _await_marker(pushed_marker)
        # Property 2: the pre-declaration window is provably open at the kill instant.
        assert not _git_commit_declared(runner_dir, lease_id), "the commit was already declared before the kill"

        runner_proc.kill()
        runner_proc.wait(timeout=10)

        # Release the fence: the orphaned worker's declare now fails against a dead
        # runner and it exits — issue #284's race, pinned rather than timed.
        go_marker.write_text("go\n")
        _wait_pid_gone(worker_pid)

        assert _session_ends(runner_dir) == set(), "the orphan recorded a session-end against a dead runner"
        assert not _git_commit_declared(runner_dir, lease_id), "the orphan's declare succeeded against a dead runner"
        _assert_invariants(runner_dir, hub_dir, when="after kill -9 in the pre-declaration window")

        runner_proc = start_runner(runner_dir, crash_point=None)
        status = wait_status(hub, chunk_id, {"done", "needs_human"})
        assert status in {"done", "needs_human"}, f"chunk did not converge after runner kill -9: {status}"
        _assert_invariants(runner_dir, hub_dir, when="after runner-daemon recovery")

        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        if status == "done":
            # D1's refusal worked through a retried build, not a silent empty delivery — against
            # the unmodified LAND_STEP this assertion fails: `done` with `commits == []`.
            assert len(commits) == 1, f"landed `done` with an empty delivery: {landed_file} is on main {len(commits)}x"
        else:
            # The refusal escalated instead of silently converging — never a landed file.
            assert len(commits) == 0, f"escalated to needs_human but {landed_file} still landed on main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- Graceful restart-resume (issue #12) — re-attach to an in-flight session in place ---


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


def _lease_for_chunk(runner_dir: Path, chunk_id: str, *, timeout: float = 30.0) -> tuple[str, int, str, int]:
    """Poll until ``chunk_id`` has exactly one spawned lease, and return
    ``(lease_id, epoch, session_id, pid)`` — the identity a deterministic-kill case pins against."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = _leases_for_chunk(runner_dir, chunk_id)
        if len(rows) == 1 and rows[0][2] is not None and rows[0][3] is not None:
            lease_id, epoch, session_id, pid = rows[0]
            assert session_id is not None and pid is not None
            return lease_id, epoch, session_id, pid
        time.sleep(0.1)
    raise AssertionError(f"chunk {chunk_id} never got a spawned lease within {timeout}s")


def _wait_session_ended(runner_dir: Path, lease_id: str, *, timeout: float = 30.0) -> None:
    """Block until ``lease_id``'s ``SessionEnd`` fact is durable."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lease_id in _session_ends(runner_dir):
            return
        time.sleep(0.05)
    raise AssertionError(f"lease {lease_id} never recorded a session-end within {timeout}s")


def _git_commit_declared(runner_dir: Path, lease_id: str) -> bool:
    """Whether ``lease_id`` has a durable ``git_commit`` declaration."""
    store, engine = _runner_store(runner_dir)
    try:
        return bool(store.git_commit_declarations_for_lease(lease_id))
    finally:
        engine.dispose()


def _wait_pid_gone(pid: int, *, timeout: float = 30.0) -> None:
    """Block until ``pid`` is no longer a live process — an orphaned worker's exit, observed
    directly rather than inferred from a durable fact it may never get to write."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    raise AssertionError(f"orphaned worker pid {pid} did not exit within {timeout}s")


def _await_committed(runner_dir: Path, chunk_id: str, landed_file: str, *, timeout: float = 30.0) -> None:
    """Block until the mid-flight build worker has committed **and durably declared** its
    git commit (issue #143, Phase 4) — the declaration, not the bare commit, is the durable
    in-flight fact a resume relies on to submit and land."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        store, engine = _runner_store(runner_dir)
        try:
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
    raise AssertionError(f"build worker never committed+declared {landed_file} before the stop")


def test_graceful_restart_resumes_in_flight_session(crash_env: CrashEnv, tmp_path: Path) -> None:
    """A graceful runner restart re-attaches to its in-flight session in place (issue #12) —
    same lease/epoch/session, only the pid rewritten, no retry consumed, and the chunk lands
    exactly once."""
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


# --- Ungraceful restart-resume (issue #13) — crash mid-work, no graceful marker ---


def _session_ends(runner_dir: Path) -> set[str]:
    store, engine = _runner_store(runner_dir)
    try:
        return store.session_ended_lease_ids()
    finally:
        engine.dispose()


def test_kill9_runner_resumes_in_flight_session(crash_env: CrashEnv, tmp_path: Path) -> None:
    """An involuntary ``kill -9`` mid-build (no graceful marker) still re-attaches the session
    (issue #13) — startup crash-recovery finds the killed-mid-work lease itself and routes it
    to the same RESUME the graceful path uses, landing the chunk exactly once."""
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

        # kill -9 the whole tree, runner and worker, so neither a graceful resume-intent
        # marker nor a session-end fact is written — a faithful reboot mid-run.
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
    """A ``kill -9`` at a RESUME boundary (armed on the restart) still re-attaches exactly once —
    a second, unarmed restart converges to ``done`` under the same lease/epoch/session, with
    invariants green."""
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


# --- Live detach recovery (blizzard#38) — the abandon crash point ---


def _hang_once_build_script(landed_file: str, marker: Path) -> str:
    """Commit ``landed_file``, then ``hang()`` — but only the *first* time, so a fresh
    re-claim after the detach finds the marker and returns normally instead of hanging
    a second time."""
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
        "_branch = subprocess.run(\n"
        '    ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
        "    check=True, capture_output=True, text=True,\n"
        ").stdout.strip()\n"
        "_commit = subprocess.run(\n"
        '    ["git", "-C", repo, "rev-parse", "HEAD"],\n'
        "    check=True, capture_output=True, text=True,\n"
        ").stdout.strip()\n"
        'subprocess.run(["git", "-C", repo, "push", "--force-with-lease", "origin", _branch], check=True)\n'
        "subprocess.run(\n"
        '    ["blizzard", "runner", "artifact", "commit",\n'
        '     "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
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
    """Block until ``marker`` exists — proof the first attempt reached its hang, past the
    commit, so the detach it races is pinned strictly after that point rather than racing
    the pre-commit write."""
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
    """A ``kill -9`` anywhere inside the abandon — before its release, or after it and before
    the closure — recovers through restart-resume's path, not REAP's: the lease closes
    ``released``, and the chunk is re-claimable and lands exactly once (blizzard#38, #280)."""
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
        # Armed from the start, but unarmed in effect until a live PULL discovers the
        # detach and reaches `point` inside the abandon it triggers.
        runner_proc = start_runner(runner_dir, crash_point=point)

        assert wait_status(hub, chunk_id, {"running"}) == "running"
        # Wait for the marker, not just the committed file — see `_await_marker`.
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
        # at `point` — before the environments are released, or after them and before the closure.
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


# --- Operator chunk pause (issue #46) — the pause-park crash point ---


def _open_pause_parks(runner_dir: Path) -> set[str]:
    store, engine = _runner_store(runner_dir)
    try:
        return store.pause_parked_lease_ids()
    finally:
        engine.dispose()


@pytest.mark.parametrize("point", _PAUSE_SWEEP)
def test_kill9_at_pause_park_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` between a paused worker's kill and its durable park still keeps the
    claim (issue #46) — recovery parks the chunk rather than abandoning it, and the resumed
    session lands exactly once under the same lease, no retry consumed."""
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


# --- Generic hub command node (#65) — the hubnode.* per-step crash windows ---


# The land step's command, shared with the generic sweep graph's own ``deliver`` step — see
# :data:`support.LAND_STEP` for what it runs and why re-running it lands nothing twice.
_LAND_STEP = LAND_STEP

# A no-op second step, unmarked, so ``hubnode.after-marker.before-next`` has a "next"
# step to skip the marked land step in favour of.
_VERIFY_STEP = """python3 - <<'PYEOF'
print("post-land verification ran")
PYEOF
"""


def _hub_command_graph_yaml(landed_file: str) -> str:
    """A ``build -> merge(run:) -> done`` graph whose ``merge`` is a generic hub command node
    with a two-step ``run:`` list (``land`` then ``verify``), judging the reserved
    ``success``/``failure`` choices (#65); a clean run ends on ``success -> done`` (#63)."""
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
    """A ``kill -9`` inside a generic hub command node's per-step window recovers, lands once
    (#65): a crash before the step's marker is durable re-runs it safely, a crash after skips
    it, no ``hub:one-live-exec-slot`` leak."""
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


# --- Pending hub command node (#66) — the hubnode.after-poll.* between-polls window ---


# Reports the reserved ``pending`` outcome on its first poll, lands on every subsequent
# one, switching on a durable workdir sentinel that survives the crash-forced restart.
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
    """A ``build -> merge(run:) -> done`` graph whose ``merge`` hub node polls then lands
    (#66) — a brisk ``poll_interval`` and generous ``poll_timeout`` keep this scenario on
    the resume path rather than #64's timeout kick-back."""
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
    """A ``kill -9`` in a hub node's between-polls window resumes polling and lands once
    (#66) — pending-ness is derived from the durable poll fact, so recovery is just "keep
    polling", releasing the leaked slot and landing the file exactly once."""
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

        # The hub self-SIGKILLs in the between-polls window: poll fact durable, slot
        # release not yet run.
        code = wait_death(hub_proc)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")

        # Restart the hub UNARMED: pending-ness is derived from the durable poll fact, so
        # the chunk just resumes polling and lands.
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


# --- Mid-script inter-repo-push crash (#67) — the packaged default graph's own window ---

# A window the per-step `hubnode.*` registry points cannot reach: `land_default.py` loops
# over repos inside ONE `run:` step, marking each through a mid-run callback instead.

_WEB_REPO_NAME = "toy-web"
_LAND_STEP_COMMAND = "python3 -m blizzard.hub.graphs.scripts.land_default"


def _two_repo_build_script(landed_file: str) -> str:
    """A build node that commits ``landed_file`` in BOTH fixture repos' worktrees, then
    pushes and declares each through the real `blizzard runner artifact commit` verb (issue
    #143, Phase 4). So the chunk submits a ``git_commit`` pointer for ``toy-api`` AND
    ``toy-web`` — a genuine 2-repo land for the deliver script to loop over."""
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
        "    _branch = subprocess.run(\n"
        '        ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
        "        check=True, capture_output=True, text=True,\n"
        "    ).stdout.strip()\n"
        "    _commit = subprocess.run(\n"
        '        ["git", "-C", repo, "rev-parse", "HEAD"],\n'
        "        check=True, capture_output=True, text=True,\n"
        "    ).stdout.strip()\n"
        '    subprocess.run(["git", "-C", repo, "push", "--force-with-lease", "origin", _branch], check=True)\n'
        "    subprocess.run(\n"
        '        ["blizzard", "runner", "artifact", "commit",\n'
        '         "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
        "        check=True,\n"
        "    )\n"
    )


def _default_graph_two_repo_yaml(landed_file: str) -> str:
    """A ``build -> deliver`` graph named ``default-delivery`` whose ``deliver`` node runs
    the REAL packaged ``land_default.py`` script (not the sweep's ``true`` stand-in),
    mirroring the packaged ``default.yaml``'s ``landed -> done`` / ``conflict -> build``."""
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
    """:func:`_default_graph_two_repo_yaml`'s twin for the PR-free lane: ``deliver`` runs
    the REAL packaged ``land_ff.py`` script, which fast-forwards each repo's base branch
    directly (no PR, no merge commit) instead of ``land_default.py``'s PR merge."""
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
    """A ``kill -9`` between two repos' pushes in the real ``land_default`` re-merges only
    the unmarked repo — marker-skipped, not re-merged — landing each exactly once with no
    leaked ``hub:one-live-exec-slot`` (#67 — the verify finale's closed gap)."""
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


# --- Mid-script inter-repo-update crash for the PR-free lane — `land_ff`'s own window ---

# `land_ff.py`'s mirror of the mid-script window above: the same one-`run:`-step,
# many-repos shape.


def test_kill9_between_ff_graph_repo_pushes(crash_env: CrashEnv, tmp_path: Path) -> None:
    """A ``kill -9`` between two repos' fast-forwards in the real ``land_ff`` re-runs only
    the unmarked repo, landing each exactly once with no leaked ``hub:one-live-exec-slot`` —
    the PR-free lane's mirror of ``test_kill9_between_default_graph_repo_pushes`` (#67, #123)."""
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


# --- Operator chunk restart (#370) — the preempt kill→closure crash point ---


def _preempt_build_script(landed_file: str, marker: Path) -> str:
    """A build node that ``hang()``s the FIRST time it runs and does the real work the
    second, so the preempted attempt is genuinely in-flight and the re-entry it forces
    converges. The marker is written before the hang: a fresh session past the restart
    finds it and falls straight through to :func:`build_script`'s commit/push/declare."""
    return (
        "import pathlib\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        "if not marker.exists():\n"
        "    marker.write_text('hung once\\n')\n"
        "    hang()\n"
    ) + build_script(landed_file)


def _preempt_graph_yaml(landed_file: str, marker: Path) -> str:
    """The hang-first ``build -> deliver`` graph this scenario restarts mid-flight."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _preempt_build_script(landed_file, marker),
                "judgement": {
                    "prompt": "verdict('pass', 'committed after the restart; checks are green')\n",
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


def _ingest_preempt_chunk(hub: httpx.Client, forge: httpx.Client, landed_file: str, marker: Path) -> str:
    """Mint the hang-first graph and ingest a fresh issue against it to a ready chunk."""
    minted = hub.post("/api/graphs", json={"definition_yaml": _preempt_graph_yaml(landed_file, marker)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a preempt-crash chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # rests not-ready otherwise
    return chunk_id


@pytest.mark.parametrize("point", _PREEMPT_SWEEP)
def test_kill9_at_preempt_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` between a restarted chunk's worker kill and its ``preempted`` closure
    still costs the node no retry (#370): the hub's fence is durable, so recovery preempts
    again rather than reaping or failing the attempt, and the chunk lands exactly once."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    marker = tmp_path / "hang-first.marker"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_preempt_chunk(hub, crash_env.forge, landed_file, marker)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        # Armed from the start: unarmed in effect until a live PULL discovers the restart's
        # fence and reaches `point` inside the preempt it triggers.
        runner_proc = start_runner(runner_dir, crash_point=point)

        assert wait_status(hub, chunk_id, {"running"}) == "running"
        _await_marker(marker)  # the worker is provably inside its hang, not racing the spawn
        before = _leases_for_chunk(runner_dir, chunk_id)
        assert len(before) == 1, f"expected one lease before the restart, got {before}"
        lease_id, _epoch, session_id, pid_before = before[0]
        assert session_id and pid_before is not None

        # The operator restarts the running chunk — a claim-keeping move, not a detach.
        restarted = hub.post(f"/api/chunks/{chunk_id}/restart", json={"by": "crash-sweep"})
        assert restarted.status_code == 202, restarted.text

        # The armed runner's next PULL kills the hung worker and self-SIGKILLs before the closure.
        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")
        assert _closure_reason(runner_dir, lease_id) is None, "the closure was durable — the point fired too late"
        assert _session_ends(runner_dir) == set(), "a SIGKILL'd worker must record no session-end"

        # Restart UNARMED: the fence still stands at the hub, so recovery must reach the same
        # preempt — never a reap or a failure, either of which would spend the node's budget.
        runner_proc = start_runner(runner_dir, crash_point=None)
        reason = _wait_for_closure(runner_dir, lease_id)
        assert reason == "preempted", (
            f"the restarted chunk's lease closed {reason!r}, not 'preempted' — recovery spent a "
            "retry on an attempt the operator superseded (issue #370's budget promise regressed?)"
        )
        assert _open_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"

        # The claim was kept throughout: the same runner re-enters and finishes the work.
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"
        after = _leases_for_chunk(runner_dir, chunk_id)
        reasons = sorted(filter(None, (_closure_reason(runner_dir, row[0]) for row in after)))
        assert reasons == ["preempted", "transitioned"], f"unexpected closure set after the restart: {reasons}"

        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- The close-intent outbox (blizzard#383) — no forge: driven entirely through the ---
# --- built-in `hub` work source, whose landing marker and whose closer are both local ---


def _close_intent_graph_yaml() -> str:
    """Named ``default-delivery`` so ``ensure_default`` resolves to it. A no-op ``build``
    hands off to ``land``, a hub node whose ``run:`` step marks ``merged/<repo>`` with no
    git or forge involved and routes straight to ``done`` — the runner must claim ``build``
    once before it holds a hub node (#65), so both ``close.*`` windows open only after that."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "pass\n",
                "judgement": {
                    "prompt": "verdict('pass', 'nothing to build')\n",
                    "choices": {"pass": {"description": "Nothing to build; hand off to land.", "to": "land"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "land": {
                "executor": "hub",
                "run": [{"command": "true", "produces": f"merged/{REPO_NAME}"}],
                "judgement": {
                    "choices": {
                        "success": {"description": "Delivered.", "to": "done"},
                        "failure": {"description": "Failed to deliver.", "to": "land"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_close_intent_chunk(hub: httpx.Client) -> tuple[str, str]:
    """Mint the close-intent graph as the packaged default's own name, then create a
    hub-owned work item (issue #360/#359) — no forge issue, no configured work source at
    all. Returns ``(chunk_id, ref)``."""
    minted = hub.post("/api/graphs", json={"definition_yaml": _close_intent_graph_yaml()})
    assert minted.status_code == 201, minted.text
    created = hub.post("/api/work-sources/hub/items", json={"title": "close-intent crash chunk", "body": "b"})
    assert created.status_code == 201, created.text
    body = created.json()
    chunk_id, ref = body["chunk_id"], body["ref"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id, ref


def _wait_item_delivered(hub: httpx.Client, ref: str, *, timeout: float) -> dict | None:
    """Poll the hub-owned item until its closure is durable — the drain sweep's own
    cadence, not a request/response round trip (D3: unconditional, no config knob to
    shorten). Returns the last-read item, or ``None`` if it never answered."""
    deadline = time.monotonic() + timeout
    item = None
    while time.monotonic() < deadline:
        resp = hub.get(f"/api/work-sources/hub/items/{ref}")
        if resp.status_code == 200:
            item = resp.json()
            if item.get("closure") == "delivered":
                return item
        time.sleep(0.5)
    return item


@pytest.mark.parametrize("point", _CLOSE_SWEEP)
def test_kill9_at_close_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` inside the close-intent outbox's own windows (blizzard#383) still
    converges: the pending intent survives the crash and the item closes exactly once,
    driven entirely through the built-in ``hub`` work source — no forge involved."""
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # Both close.* windows fire inside the HUB; no configured work source at all — the
    # always-seated built-in `hub` source is the only one.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point, work_sources=())
    runner_proc = None
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id, ref = _ingest_close_intent_chunk(hub)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        # The runner's own poll loop is what drives the held chunk's hub-advance calls
        # (#65/#66) — no work for it to claim otherwise, since `land` is hub-executed.
        runner_proc = start_runner(runner_dir, crash_point=None)

        code = wait_death(hub_proc)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}")

        # Restart the hub UNARMED; the runner's replayed hub-advance poll resumes the
        # interrupted run, and the always-on drain sweep retires the surviving intent.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, work_sources=())
        await_http(hub, "/api/health", proc=hub_proc)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}")

        item = _wait_item_delivered(hub, ref, timeout=60.0)
        assert item is not None and item.get("closure") == "delivered", (
            f"item {ref} did not close after kill at {point} (last read: {item!r})"
        )

        # Exactly-once: the drain never double-closes a ref it already retired.
        hub_engine = create_engine_from_url(HubConfig.load(hub_dir).db_url)
        with hub_engine.connect() as conn:
            outcomes = conn.execute(
                select(hub_schema.work_item_closures.c.outcome).where(
                    (hub_schema.work_item_closures.c.chunk_id == chunk_id)
                    & (hub_schema.work_item_closures.c.source == "hub")
                    & (hub_schema.work_item_closures.c.ref == ref)
                    & (hub_schema.work_item_closures.c.outcome.in_(["closed", "gone"]))
                )
            ).all()
        assert len(outcomes) == 1, f"ref {ref} carries {len(outcomes)} terminal closure outcomes, not exactly one"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)
