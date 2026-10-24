"""The kill-9 sweep (``blizzard:crash-sweep``) — MVP acceptance criterion 4.

For each crash point in the registry (``bzh:crash-point-registry``): arms it so the
owning daemon SIGKILLs itself there, restarts unarmed, and asserts invariants stay
green and the chunk lands exactly once. Families the generic scenario can't reach get
a dedicated scenario each. Gated by ``BLIZZARD_CRASH_SWEEP=1``; see ``conftest.py``."""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sqlalchemy import Engine, Row, insert, select

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.node_steps import Executor
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE, hub_token_variable
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.app import CLOSE_DRAIN_INTERVAL_SECONDS
from blizzard.hub.domain.tenancy.teardown import DRAIN_BATCH_ROWS
from blizzard.hub.store import schema as hub_schema
from blizzard.hub.store.scoped import is_tenant_owned
from blizzard.hub.tenancy import TenantId
from blizzard.runner.composition import build_host_stores
from blizzard.runner.config import DEFAULT_WORKSPACE_ID
from blizzard.runner.environments.internal.basic_provider import BasicWorkspaceProvider
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.token_file import assigned_token
from blizzard.runner.leases.model import NewLease
from blizzard.runner.store import schema as runner_schema
from blizzard.runner.throttle.settings import HoldOrRelease, ThrottleSettings, UtilizationSettings
from blizzard.tools.invariants import Invariants
from tests.config_inputs import init_host, load_host_config, load_hub_config
from tests.crash.support import (
    ENV_CRASH_FENCE,
    ENV_CRASH_POINT,
    OPENCODE_SESSION_NAME,
    OWNER,
    REPO,
    REPO_NAME,
    CrashEnv,
    await_host,
    await_http,
    build_script,
    checks_graph_yaml,
    git_bare,
    graph_yaml,
    intended_migrate_source_yaml,
    land_node,
    migrate_hub_source_yaml,
    migrate_hub_target_yaml,
    migrate_source_yaml,
    migrate_target_yaml,
    nudge_graph_yaml,
    opencode_build_script,
    opencode_graph_yaml,
    pre_declare_build_script,
    runner_api,
    sigint_trap_hang_script,
    start_hub,
    start_runner,
    terminate,
    usage_limited_graph_yaml,
    usage_limited_judge_graph_yaml,
    wait_death,
    wait_status,
    write_runner_config,
)
from tests.crash_points import discover_crash_points
from tests.fleet_world import BUILD_BRANCH, scripted_build, world_repos
from tests.host_files import load_runner_config, with_runner
from tests.otlp_sink import OtlpSink, otlp_sink
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    SqlAlchemyRunnerStore,
    daemon_store,
    registered_fixture_store,
    runner_store_errors,
)
from tests.runner_ingest_support import RUNNER_TABLES, contents, invariant_violations, seed_runner
from tests.runner_join import RUNNER_BIN, runner_spawn_env
from tests.support import create_repositories, fixture_repository, free_port, hub_scoped, seed_drainable_history
from tests.support_child_launch import env_git

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
    "action.",
    "migrate.",
    "attach.",
    "nudge.",
    "declare-commit.",
    "checks.",
    "preempt.",
    "close.",
    "usagelimit.",
    "trace.",
    "leasetrace.",
    "hostlane.",
    "egress.",
    "teardown.",
    "ingest.",
    "release.",
)
_RESUME_POINTS = [p for p in _ALL_POINTS if p.startswith("resume.")]
_ABANDON_POINTS = [p for p in _ALL_POINTS if p.startswith("abandon.")]
_PAUSE_POINTS = [p for p in _ALL_POINTS if p.startswith("pause.")]
# `hubnode.*` (#65) and its narrower `hubnode.after-poll.` carve-out (#66) fire inside the
# hub's synchronous executor; each is swept by its own dedicated test below.
_HUBNODE_PENDING_POINTS = [p for p in _ALL_POINTS if p.startswith("hubnode.after-poll.")]
_HUBNODE_POINTS = [p for p in _ALL_POINTS if p.startswith("hubnode.") and p not in _HUBNODE_PENDING_POINTS]
# `action.*` fires inside the hub's action pass driver, as a pass's effect is written. Swept by
# `test_kill9_at_action_crash_point`.
_ACTION_POINTS = [p for p in _ALL_POINTS if p.startswith("action.")]
# `migrate.*` fires inside the HUB on a cross-graph judgement choice. Swept by
# `test_kill9_at_migrate_crash_point`.
_MIGRATE_POINTS = [p for p in _ALL_POINTS if p.startswith("migrate.")]
# `attach.*` fires on the RUNNER's out-of-band attach endpoint. Swept by
# `test_kill9_at_attach_crash_point`.
_ATTACH_POINTS = [p for p in _ALL_POINTS if p.startswith("attach.")]
# `declare-commit.*` fires on the RUNNER's out-of-band declare endpoint, `attach.*`'s
# sibling. Swept by `test_kill9_at_declare_commit_crash_point`.
_DECLARE_COMMIT_POINTS = [p for p in _ALL_POINTS if p.startswith("declare-commit.")]
# `nudge.*` fires in the RUNNER's ADVANCE step for an unattached `produces:` name. Swept by
# `test_kill9_at_nudge_crash_point`.
_NUDGE_POINTS = [p for p in _ALL_POINTS if p.startswith("nudge.")]
# `checks.*` fires in the RUNNER's ADVANCE step for a node's `checks:` command (#114).
# Swept by `test_kill9_at_checks_crash_point`.
_CHECKS_POINTS = [p for p in _ALL_POINTS if p.startswith("checks.")]
# `preempt.*` fires in the RUNNER's PULL step, inside the teardown an operator restart forces
# (#370). Swept by `test_kill9_at_preempt_crash_point`.
_PREEMPT_POINTS = [p for p in _ALL_POINTS if p.startswith("preempt.")]
# `close.*` fires inside the HUB — the close-intent outbox's own enqueue-then-drain
# windows. Swept by `test_kill9_at_close_crash_point`.
_CLOSE_POINTS = [p for p in _ALL_POINTS if p.startswith("close.")]
# `usagelimit.*` fires in the RUNNER's own ADVANCE step, once it classifies an exited worker
# or judge generation as usage-limited. Swept by
# `test_kill9_at_usage_limit_crash_point`.
_USAGE_LIMIT_POINTS = [p for p in _ALL_POINTS if p.startswith("usagelimit.")]
# `trace.*` fires inside the HUB's trace export sweep, between the exporter's acceptance and
# the cursor-row append. Swept by `test_kill9_at_trace_crash_point`.
_TRACE_POINTS = [p for p in _ALL_POINTS if p.startswith("trace.")]
# `leasetrace.*`: the RUNNER's twin of `trace.*`, swept by `test_kill9_at_lease_trace_crash_point`.
_LEASE_TRACE_POINTS = [p for p in _ALL_POINTS if p.startswith("leasetrace.")]
# `hostlane.*`: the RUNNER host's own fact lane, between the hub's answer and the mark recorded; swept by
# `test_kill9_at_host_lane_crash_point`.
_HOST_LANE_POINTS = [p for p in _ALL_POINTS if p.startswith("hostlane.")]
# `egress.*` fires in the HUB's egress sweep; swept by `test_kill9_at_egress_crash_point`.
_EGRESS_POINTS = [p for p in _ALL_POINTS if p.startswith("egress.")]
# `teardown.*` fires inside the HUB's tenant deletion — after its close, between drain batches, and after its drain.
# Swept by `test_kill9_at_teardown_crash_point`.
_TEARDOWN_POINTS = [p for p in _ALL_POINTS if p.startswith("teardown.")]
# `ingest.*` fires inside `blizzard runner ingest`, between its store copy, its configuration edits and its removal
# from the source.
_INGEST_POINTS = [p for p in _ALL_POINTS if p.startswith("ingest.")]
# `release.*` fires once the hub accepts a gate-parked chunk back, between steps or from a usage-limited park.
_RELEASE_POINTS = [p for p in _ALL_POINTS if p.startswith("release.")]
_GENERIC_POINTS = [p for p in _ALL_POINTS if not p.startswith(_DEDICATED_PREFIXES)]

# A representative CI subset, one point per family, run as a bounded-runtime gate under
# `BLIZZARD_CRASH_SWEEP_CI=1`; the full sweep is the local/`release`-workflow command.
_CI_SUBSET = (
    "reap.after-expire",
    "pull.after-flush",
    "fill.after-bind.before-claim",
    "spawn.after-lease-mint.before-spawn",
    # The two-phase spawn's three windows: recovery-critical enough to earn their own CI coverage.
    "spawn.after-launch.before-provisional-record",
    "spawn.after-provisional-record.before-identity",
    "spawn.after-identity.before-session-record",
    "advance.after-buffer.before-flush",
    "flush.after-submit.before-ack",
    # `claim.*` is a boundary family within `_GENERIC_POINTS`; a family's lone
    # member is its own CI representative.
    "claim.after-persist.before-response",
    # `transcript.*`: reachable here with no dedicated scenario — every
    # lease closure enqueues a final marker regardless of `[transcripts] ship`.
    "transcript.after-submit.before-ack",
)

# The resume CI subset: the recovery-critical kill-first window, bounding wall time
# (each resume case restarts the runner twice).
_RESUME_CI_SUBSET = ("resume.after-kill.before-reattach",)

# The abandon CI subset, bounding wall time at one case for a two-member family:
# `after-release.before-closure` is the strictly-later state of this same scenario, so the earlier
# window is the wider arm. The full sweep runs both; only CI drops one.
_ABANDON_CI_SUBSET = ("abandon.after-kill.before-release",)

# The pause CI subset (#46): the family's lone point, the regression fence on the
# issue's central bug.
_PAUSE_CI_SUBSET = ("pause.after-interrupt.before-park",)

# The hub command node CI subset (#65): the family's first-declared member is its own CI
# representative.
_HUBNODE_CI_SUBSET = ("hubnode.after-step.before-marker",)

# The hub-node pending CI subset (#66): the narrower between-polls window carved out of the
# `hubnode.*` family above; its lone member is its own CI representative.
_HUBNODE_PENDING_CI_SUBSET = ("hubnode.after-poll.before-slot-release",)
_ACTION_CI_SUBSET = ("action.after-effect.before-write",)

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

# The close CI subset: the recovery-critical member — the drain's own
# after-close, before-record window — is its own CI representative.
_CLOSE_CI_SUBSET = ("close.after-close.before-record",)

# The usage-limit CI subset: the worker-side window is the family's
# recovery-critical member — the mark is durable, the park is not yet.
_USAGE_LIMIT_CI_SUBSET = ("usagelimit.worker-after-mark.before-park",)


# The trace CI subset: the family's lone member is its own CI representative.
_TRACE_CI_SUBSET = ("trace.after-export.before-cursor",)

# The lease trace CI subset: the family's lone member is its own CI representative.
_LEASE_TRACE_CI_SUBSET = ("leasetrace.after-export.before-cursor",)


# The host-lane CI subset: the family's lone member is its own CI representative.
_HOST_LANE_CI_SUBSET = ("hostlane.after-push.before-mark",)


# The egress CI subset: the later window; the full sweep runs both.
_EGRESS_CI_SUBSET = ("egress.after-commit.before-cursor",)

# The teardown CI subset: the mid-drain window, where a committed batch must be neither redone nor counted twice.
_TEARDOWN_CI_SUBSET = ("teardown.after-batch.before-next",)

# The ingest CI subset: the later window, where the source has lost its token but still holds the runner's rows.
_INGEST_CI_SUBSET = ("ingest.after-config.before-removal",)

# The release CI subset: the between-steps window; the full sweep runs the usage-limited one too.
_RELEASE_CI_SUBSET = ("release.after-hub.before-environments",)


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
_ACTION_SWEEP = _select(_ACTION_POINTS, _ACTION_CI_SUBSET)
_MIGRATE_SWEEP = _select(_MIGRATE_POINTS, _MIGRATE_CI_SUBSET)
_ATTACH_SWEEP = _select(_ATTACH_POINTS, _ATTACH_CI_SUBSET)
_NUDGE_SWEEP = _select(_NUDGE_POINTS, _NUDGE_CI_SUBSET)
_CHECKS_SWEEP = _select(_CHECKS_POINTS, _CHECKS_CI_SUBSET)
_DECLARE_COMMIT_SWEEP = _select(_DECLARE_COMMIT_POINTS, _DECLARE_COMMIT_CI_SUBSET)
_PREEMPT_SWEEP = _select(_PREEMPT_POINTS, _PREEMPT_CI_SUBSET)
_CLOSE_SWEEP = _select(_CLOSE_POINTS, _CLOSE_CI_SUBSET)
_USAGE_LIMIT_SWEEP = _select(_USAGE_LIMIT_POINTS, _USAGE_LIMIT_CI_SUBSET)
_TRACE_SWEEP = _select(_TRACE_POINTS, _TRACE_CI_SUBSET)
_LEASE_TRACE_SWEEP = _select(_LEASE_TRACE_POINTS, _LEASE_TRACE_CI_SUBSET)
_HOST_LANE_SWEEP = _select(_HOST_LANE_POINTS, _HOST_LANE_CI_SUBSET)
_EGRESS_SWEEP = _select(_EGRESS_POINTS, _EGRESS_CI_SUBSET)
_TEARDOWN_SWEEP = _select(_TEARDOWN_POINTS, _TEARDOWN_CI_SUBSET)
_INGEST_SWEEP = _select(_INGEST_POINTS, _INGEST_CI_SUBSET)
_RELEASE_SWEEP = _select(_RELEASE_POINTS, _RELEASE_CI_SUBSET)


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
        | set(_select(_ACTION_POINTS, _ACTION_CI_SUBSET))
        | set(_select(_MIGRATE_POINTS, _MIGRATE_CI_SUBSET))
        | set(_select(_ATTACH_POINTS, _ATTACH_CI_SUBSET))
        | set(_select(_NUDGE_POINTS, _NUDGE_CI_SUBSET))
        | set(_select(_CHECKS_POINTS, _CHECKS_CI_SUBSET))
        | set(_select(_DECLARE_COMMIT_POINTS, _DECLARE_COMMIT_CI_SUBSET))
        | set(_select(_PREEMPT_POINTS, _PREEMPT_CI_SUBSET))
        | set(_select(_CLOSE_POINTS, _CLOSE_CI_SUBSET))
        | set(_select(_USAGE_LIMIT_POINTS, _USAGE_LIMIT_CI_SUBSET))
        | set(_select(_TRACE_POINTS, _TRACE_CI_SUBSET))
        | set(_select(_LEASE_TRACE_POINTS, _LEASE_TRACE_CI_SUBSET))
        | set(_select(_HOST_LANE_POINTS, _HOST_LANE_CI_SUBSET))
        | set(_select(_EGRESS_POINTS, _EGRESS_CI_SUBSET))
        | set(_select(_TEARDOWN_POINTS, _TEARDOWN_CI_SUBSET))
        | set(_select(_INGEST_POINTS, _INGEST_CI_SUBSET))
        | set(_select(_RELEASE_POINTS, _RELEASE_CI_SUBSET))
    )
    uncovered = {family for family in families if not any(p.startswith(f"{family}.") for p in ci_selected)}
    assert not uncovered, f"registry families with zero CI-subset coverage: {sorted(uncovered)}"


# --- The OpenCode-lineage windows — already-declared harness-neutral points a
# Claude-Code-shaped graph never reaches under an OpenCode lineage, not a new family. ---

# Derived by prefix filter against `_ALL_POINTS`, like every sibling family above, so a
# newly-registered point is never silently missing its OpenCode arm.
_OPENCODE_GENERIC_POINTS = [p for p in _ALL_POINTS if p.startswith(("spawn.", "advance."))]
_OPENCODE_RESUME_POINTS = [p for p in _ALL_POINTS if p.startswith("resume.")]

# Each mirrors its Claude-Code sibling's own CI representative (`_CI_SUBSET`/`_RESUME_CI_SUBSET`)
# — the same harness-neutral point, proven recoverable under an OpenCode lineage in CI too.
_OPENCODE_GENERIC_CI_SUBSET: tuple[str, ...] = ("spawn.after-lease-mint.before-spawn",)
_OPENCODE_RESUME_CI_SUBSET: tuple[str, ...] = ("resume.after-kill.before-reattach",)
_OPENCODE_GENERIC_SWEEP = _select(_OPENCODE_GENERIC_POINTS, _OPENCODE_GENERIC_CI_SUBSET)
_OPENCODE_RESUME_SWEEP = _select(_OPENCODE_RESUME_POINTS, _OPENCODE_RESUME_CI_SUBSET)


#: Hand-authored, not registry-filtered — `_RESUME_POINTS` shares `_OPENCODE_RESUME_POINTS`'s
#: exact predicate, so only an independent anchor catches either narrowing.
_RESUME_POINT_NAMES_BY_HAND = frozenset(
    {
        "resume.before-reattach",
        "resume.after-kill.before-reattach",
        "resume.after-reattach",
        "resume.wake.after-launch.before-record",
        "resume.wake.after-record",
        "resume.wake.after-boundary-record.before-launch",
    }
)


def test_opencode_named_points_are_swept_under_both_harnesses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each window this phase closes under OpenCode is armed once per harness. The
    expected set is re-derived off the registry, never read back off the two tuples
    under test, so narrowing either fails loudly instead of passing by construction."""
    monkeypatch.delenv("BLIZZARD_CRASH_SWEEP_CI", raising=False)
    assert set(_RESUME_POINT_NAMES_BY_HAND) == set(_RESUME_POINTS), (
        "the hand-authored resume-point anchor has drifted from the registry — update "
        f"_RESUME_POINT_NAMES_BY_HAND: {sorted(set(_RESUME_POINT_NAMES_BY_HAND) ^ set(_RESUME_POINTS))}"
    )
    # Generic: off `_GENERIC_POINTS`, never `_OPENCODE_GENERIC_POINTS`'s own filter.
    # Resume: off the hand-authored literal above, never `_RESUME_POINTS`'s.
    should_cover_generic = [p for p in _GENERIC_POINTS if p.startswith(("spawn.", "advance."))]
    should_cover_resume = [p for p in _ALL_POINTS if p in _RESUME_POINT_NAMES_BY_HAND]
    should_cover = should_cover_generic + should_cover_resume
    assert should_cover_generic and should_cover_resume, (
        "the registry lost every spawn./advance. or every resume. point — nothing to sweep"
    )

    # The Claude Code leg: every point is in the unnarrowed registry lists the generic/
    # resume sweeps parametrize from, reached under Claude Code there.
    claude_generic = set(_select(_GENERIC_POINTS, _CI_SUBSET))
    claude_resume = set(_select(_RESUME_POINTS, _RESUME_CI_SUBSET))
    missing_claude = [p for p in should_cover if p not in claude_generic and p not in claude_resume]
    assert not missing_claude, f"OpenCode-lineage point(s) missing their Claude-Code leg: {missing_claude}"

    # The OpenCode leg: every point is in the tuples the OpenCode-lineage tests actually
    # parametrize from, recomputed fresh rather than read off the frozen module sweeps.
    opencode_generic = set(_select(_OPENCODE_GENERIC_POINTS, _OPENCODE_GENERIC_CI_SUBSET))
    opencode_resume = set(_select(_OPENCODE_RESUME_POINTS, _OPENCODE_RESUME_CI_SUBSET))
    missing_opencode = [p for p in should_cover if p not in opencode_generic and p not in opencode_resume]
    assert not missing_opencode, f"registry point(s) missing their own OpenCode sweep arm: {missing_opencode}"


def _assert_invariants(runner_dir: Path, hub_dir: Path, *, when: str, after_recovery: bool) -> None:
    """``when`` is prose only, for the assertion message — ``after_recovery`` is each call
    site's own explicit signal, never inferred from it. The raw, un-reconciled snapshot the
    instant a kill lands passes ``False``: ``ActiveLeaseProcessIsLive`` does not hold
    there by construction. A checkpoint reached only once REAP/RESUME reconciled it passes
    ``True``, so that check is asked for there."""
    runner_db = load_runner_config(runner_dir).db_url
    hub_db = load_hub_config(hub_dir).db_url
    violations = Invariants(runner_db_url=runner_db, hub_db_url=hub_db).run(after_recovery=after_recovery)
    assert not violations, f"invariant violations {when}:\n" + "\n".join(str(v) for v in violations)


def _ingest_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
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


# `claim.*` fires inside the HUB — the one `_GENERIC_POINTS` family that
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
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        # Restart the killed daemon unarmed (startup = REAP first, for the runner) and
        # let it converge.
        if on_hub:
            hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
            await_http(hub, "/api/health", proc=hub_proc)
        else:
            runner_proc = start_runner(runner_dir, crash_point=None)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"

        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)

        # Exactly-once delivery: the file is reachable from bare main exactly once.
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


def _ingest_migrate_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> tuple[str, str]:
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
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        # The migration is durable even though the MIGRATED response never returned.
        hub_engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
        with hub_engine.connect() as conn:
            migrations = conn.execute(
                hub_scoped(
                    select(hub_schema.chunk_migrations).where(hub_schema.chunk_migrations.c.chunk_id == chunk_id)
                )
            ).all()
        assert len(migrations) == 1, "the migration fact was not durably recorded before the crash"

        # Restart the hub UNARMED; the runner's replayed completion re-derives MIGRATED and
        # the chunk re-queues + lands under the target graph.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)

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


def _ingest_intended_migrate_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> tuple[str, str]:
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
    """A ``kill -9`` right after an **intended** migration is recorded still recovers —
    the intent is durably cleared in the same transaction as the migration
    fact, so recovery never re-fires it, and the target's build + deliver lands once."""
    landed_file = f"LANDED-INTENDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # Arm the HUB: the migrate window opens inside its completions handler, not the runner.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(
            runner_dir, hub_dir, when=f"immediately after intended-migration kill at {point}", after_recovery=False
        )

        # The intent must be cleared in the SAME transaction as the migration fact — a
        # crash that recorded one but not the other would re-fire the migration on recovery.
        hub_engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
        with hub_engine.connect() as conn:
            migrations = conn.execute(
                hub_scoped(
                    select(hub_schema.chunk_migrations).where(hub_schema.chunk_migrations.c.chunk_id == chunk_id)
                )
            ).all()
            intent = conn.execute(
                hub_scoped(
                    select(hub_schema.chunks.c.intended_migration).where(hub_schema.chunks.c.chunk_id == chunk_id)
                )
            ).scalar_one()
        assert len(migrations) == 1, "the migration fact was not durably recorded before the crash"
        assert intent is None, "the intent was not durably cleared alongside the migration fact"

        # Restart the hub UNARMED; the runner's replayed completion re-derives MIGRATED and
        # the chunk re-queues + lands under the target graph — the intent never re-fires.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(
            runner_dir, hub_dir, when=f"after intended-migration convergence past {point}", after_recovery=True
        )

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


def _ingest_migrate_hub_chunk(hub: httpx2.Client, forge: httpx2.Client, title: str) -> tuple[str, str]:
    """Mint the hub-landing migrate target + source graphs, file a fresh
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
    still recovers — the retained route and derived ``delivering`` state
    let the holding runner's ADVANCE poll carry the chunk to ``done`` without wedging."""
    title = f"HUB-MIGRATE-{point.replace('.', '_')}"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # Arm the HUB: the migrate window opens inside its completions handler, not the runner.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(
            runner_dir, hub_dir, when=f"immediately after hub-landing kill at {point}", after_recovery=False
        )

        # The migration is durable even though the response never returned — and it landed on
        # the hub-executed node, so the chunk derives `delivering`, never `ready`.
        hub_engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
        with hub_engine.connect() as conn:
            migrations = conn.execute(
                hub_scoped(
                    select(hub_schema.chunk_migrations).where(hub_schema.chunk_migrations.c.chunk_id == chunk_id)
                )
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
        _assert_invariants(runner_dir, hub_dir, when=f"after hub-landing convergence past {point}", after_recovery=True)

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
    full provenance readable against the same store after the ungraceful death."""
    runner_dir = tmp_path / "runner"
    # Nothing listens on ``hub_port`` — the attach path never calls the hub; the loop's hub
    # polls just fail and are swallowed, and the local API serves regardless.
    hub_port, runner_port = free_port(), free_port()
    write_runner_config(
        runner_dir,
        workspace=crash_env.workspace,
        bin_dir=crash_env.bin_dir,
        hub_port=hub_port,
        port=runner_port,
        join=False,
    )
    db_url = load_runner_config(runner_dir).db_url

    # Seed a lease + its capability token, then park it, through a store the daemon does not
    # yet hold; dispose so the hosted daemon opens the sqlite file with no concurrent writer.
    engine = create_engine_from_url(db_url)
    store = registered_fixture_store(engine)
    store.record_lease(
        NewLease(
            lease_id="lease_attach",
            chunk_id="ch_attach",
            graph_id="gr_attach",
            node_id="nd_review",
            node_name="review",
            epoch=4,
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
        session=None,
        asked_at=_ATTACH_NOW,
    )
    store.record_park(lease_id="lease_attach", chunk_id="ch_attach", question_id="q_park", parked_at=_ATTACH_NOW)
    engine.dispose()

    runner = runner_api(runner_dir, runner_port)
    runner_proc = start_runner(runner_dir, crash_point=point)
    try:
        await_host(runner_port, runner_proc)

        # The runner records the attachment durably, then self-SIGKILLs before the response —
        # the client sees the killed connection, never a 200.
        with pytest.raises(httpx2.HTTPError):
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
            assert SqlAlchemyRunnerStore(engine2, runner_store_errors()).attachments_for_lease("lease_attach") == {
                _ATTACH_NAME: _ATTACH_CONTENT
            }
            with engine2.connect() as conn:
                rows = conn.execute(
                    select(runner_schema.attachments).where(
                        runner_schema.attachments.c.runner_id == FIXTURE_RUNNER_ID,
                        runner_schema.attachments.c.lease_id == "lease_attach",
                    )
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
        await_host(runner_port, runner_proc)
        engine3 = create_engine_from_url(db_url)
        try:
            assert SqlAlchemyRunnerStore(engine3, runner_store_errors()).attachments_for_lease("lease_attach") == {
                _ATTACH_NAME: _ATTACH_CONTENT
            }
        finally:
            engine3.dispose()
    finally:
        runner.close()
        terminate(runner_proc)


_DECLARE_COMMIT_TOKEN = "the-declare-commit-lease-token"
# The env the basic provider allocates for the seeded chunk (named by the chunk id) and its repo — the
# declare edge checks the repo against that env's real manifest, so these must be the actual ones.
_DECLARE_COMMIT_ENV = "ch_declare_commit"
_DECLARE_COMMIT_REPO = REPO_NAME
_DECLARE_COMMIT_BRANCH = "feat/declare-commit"
_DECLARE_COMMIT_SHA = "deadbeefcafef00d"
_DECLARE_COMMIT_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("point", _DECLARE_COMMIT_SWEEP)
def test_kill9_at_declare_commit_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` right after the runner records a worker git-commit declaration keeps
    it, with full provenance readable against the same store after the ungraceful death."""
    runner_dir = tmp_path / "runner"
    # Materialize the env's worktrees directly (this scenario seeds its lease + binding,
    # skipping FILL's acquire), through the same basic provider the daemon uses.
    [acquired] = BasicWorkspaceProvider(
        str(crash_env.workspace), repos=world_repos(crash_env.workspace), max_environments=1, git=env_git()
    ).acquire("ch_declare_commit", 1, [])
    # Nothing listens on ``hub_port`` — the declare path never calls the hub; the loop's
    # hub polls just fail and are swallowed, and the local API serves regardless.
    hub_port, runner_port = free_port(), free_port()
    write_runner_config(
        runner_dir,
        workspace=crash_env.workspace,
        bin_dir=crash_env.bin_dir,
        hub_port=hub_port,
        port=runner_port,
        join=False,
    )
    db_url = load_runner_config(runner_dir).db_url

    # Seed a lease + its capability token, then park it, through a store the daemon does not
    # yet hold; dispose so the hosted daemon opens the sqlite file with no concurrent writer.
    engine = create_engine_from_url(db_url)
    store = registered_fixture_store(engine)
    store.record_lease(
        NewLease(
            lease_id="lease_declare_commit",
            chunk_id="ch_declare_commit",
            graph_id="gr_declare_commit",
            node_id="nd_build",
            node_name="build",
            epoch=4,
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
        workdir=acquired.workdir,
        bound_at=_DECLARE_COMMIT_NOW,
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    store.record_ask(
        lease_id="lease_declare_commit",
        chunk_id="ch_declare_commit",
        question_id="q_park",
        question="parked so REAP leaves the seeded lease be",
        options=[],
        session=None,
        asked_at=_DECLARE_COMMIT_NOW,
    )
    store.record_park(
        lease_id="lease_declare_commit",
        chunk_id="ch_declare_commit",
        question_id="q_park",
        parked_at=_DECLARE_COMMIT_NOW,
    )
    engine.dispose()

    runner = runner_api(runner_dir, runner_port)
    runner_proc = start_runner(runner_dir, crash_point=point)
    try:
        await_host(runner_port, runner_proc)

        # The runner records the declaration durably, then self-SIGKILLs before the
        # response — the client sees the killed connection, never a 200.
        with pytest.raises(httpx2.HTTPError):
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
            declarations = SqlAlchemyRunnerStore(engine2, runner_store_errors()).git_commit_declarations_for_lease(
                "lease_declare_commit"
            )
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
                        runner_schema.git_commit_declarations.c.runner_id == FIXTURE_RUNNER_ID,
                        runner_schema.git_commit_declarations.c.lease_id == "lease_declare_commit",
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
        await_host(runner_port, runner_proc)
        engine3 = create_engine_from_url(db_url)
        try:
            declarations = SqlAlchemyRunnerStore(engine3, runner_store_errors()).git_commit_declarations_for_lease(
                "lease_declare_commit"
            )
            assert set(declarations) == {(_DECLARE_COMMIT_ENV, _DECLARE_COMMIT_REPO)}
        finally:
            engine3.dispose()
    finally:
        runner.close()
        terminate(runner_proc)


def _ingest_nudge_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
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
    and the chunk still landing exactly once."""
    landed_file = f"NUDGE-LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_nudge_chunk(hub, crash_env.forge, landed_file)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=point)

        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"

        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        runner_proc = start_runner(runner_dir, crash_point=None)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"

        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)

        # Exactly-once delivery, as every scenario asserts.
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


def _ingest_checks_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
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
    once and ``runner:checks-recorded-when-marked`` green."""
    landed_file = f"CHECKS-LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_checks_chunk(hub, crash_env.forge, landed_file)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=point)

        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"

        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        runner_proc = start_runner(runner_dir, crash_point=None)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"

        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)

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
    pre-declaration-window scenario's build node (``bzh:crash-sweep`` case 2)."""
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
            "deliver": land_node(),
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_pre_declare_chunk(
    hub: httpx2.Client, forge: httpx2.Client, landed_file: str, pushed_marker: Path, go_marker: Path
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
    (``_crash_orphaned``'s ``ended`` skip), pinned deterministically (``bzh:crash-sweep``)."""
    landed_file = "LANDED-runner-after-session-end.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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

        _assert_invariants(
            runner_dir, hub_dir, when="after external kill -9 following the worker's session-end", after_recovery=True
        )

        runner_proc = start_runner(runner_dir, crash_point=None)
        assert runner_proc.pid != pid_before
        assert wait_status(hub, chunk_id, {"done"}) == "done", "chunk did not converge after runner kill -9"
        _assert_invariants(runner_dir, hub_dir, when="after runner-daemon recovery", after_recovery=True)

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
    commit — the pre-declaration race, pinned deterministically — must never land
    ``done`` with an empty delivery. The land step's empty-delivery refusal is what this proves."""
    landed_file = "LANDED-runner-pre-declare.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    pushed_marker = tmp_path / "pushed.marker"
    go_marker = tmp_path / "go.marker"

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        # (``bzh:crash-sweep`` property 3), not a race against the worker's pace.
        _await_marker(pushed_marker)
        # Property 2: the pre-declaration window is provably open at the kill instant.
        assert not _git_commit_declared(runner_dir, lease_id), "the commit was already declared before the kill"

        runner_proc.kill()
        runner_proc.wait(timeout=10)

        # Release the fence: the orphaned worker's declare now fails against a dead
        # runner and it exits — the race, pinned rather than timed.
        go_marker.write_text("go\n")
        _wait_pid_gone(worker_pid)

        assert _session_ends(runner_dir) == set(), "the orphan recorded a session-end against a dead runner"
        assert not _git_commit_declared(runner_dir, lease_id), "the orphan's declare succeeded against a dead runner"
        _assert_invariants(runner_dir, hub_dir, when="after kill -9 in the pre-declaration window", after_recovery=True)

        runner_proc = start_runner(runner_dir, crash_point=None)
        status = wait_status(hub, chunk_id, {"done", "needs_human"})
        assert status in {"done", "needs_human"}, f"chunk did not converge after runner kill -9: {status}"
        _assert_invariants(runner_dir, hub_dir, when="after runner-daemon recovery", after_recovery=True)

        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        if status == "done":
            # `land`'s refusal worked through a retried build, not a silent empty delivery — without the
            # refusal this assertion fails: `done` with `commits == []`.
            assert len(commits) == 1, f"landed `done` with an empty delivery: {landed_file} is on main {len(commits)}x"
        else:
            # The refusal escalated instead of silently converging — never a landed file.
            assert len(commits) == 0, f"escalated to needs_human but {landed_file} still landed on main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- Graceful restart-resume — re-attach to an in-flight session in place ---


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
            "deliver": land_node(),
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_hanging_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
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


def _sigint_drain_graph_yaml(landed_file: str) -> str:
    """:func:`_hanging_graph_yaml`'s twin: the build node's whole script runs
    behind :func:`~tests.crash.support.sigint_trap_hang_script`'s SIGINT trap, so a graceful
    shutdown's own signal lands on a script that answers it with a real envelope no matter
    which statement it catches the script mid."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": sigint_trap_hang_script(build_script(landed_file)),
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
            "deliver": land_node(),
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_sigint_drain_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
    """Mint the SIGINT-trapping graph and ingest a fresh issue against it to a ready chunk."""
    minted = hub.post("/api/graphs", json={"definition_yaml": _sigint_drain_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a shutdown-drain chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


def _usage_fact_cost(runner_dir: Path, lease_id: str, generation: int) -> float | None:
    """The recorded ``cost_usd`` for ``lease_id``'s ``generation``-th spawn/resume usage
    fact — asserts exactly one such fact exists."""
    with _audit_connection(runner_dir) as conn:
        rows = conn.execute(
            select(runner_schema.usage_facts.c.cost_usd)
            .where(runner_schema.usage_facts.c.lease_id == lease_id)
            .where(runner_schema.usage_facts.c.generation == generation)
            .where(runner_schema.usage_facts.c.kind.in_(("spawn", "resume")))
        ).all()
    assert len(rows) == 1, f"expected exactly one spawn/resume usage fact for {lease_id} gen {generation}: {rows}"
    return rows[0][0]


def _runner_store(runner_dir: Path) -> tuple[SqlAlchemyRunnerStore, Engine]:
    """A read store over the runner's sqlite, bound to the runner its registration stored, plus its engine (dispose
    after use)."""
    engine = create_engine_from_url(load_runner_config(runner_dir).db_url)
    return daemon_store(engine), engine


@contextlib.contextmanager
def _audit_connection(runner_dir: Path) -> Iterator[Any]:
    """A read connection over every runner's rows in the runner's sqlite, as the invariant checker's audit opens one."""
    engine = create_engine_from_url(load_runner_config(runner_dir).db_url)
    try:
        audit = build_host_stores(engine, errors=runner_store_errors(), clock=SystemClock()).runner_audit
        with audit.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def _leases_for_chunk(runner_dir: Path, chunk_id: str) -> list[tuple[str, int, str | None, int | None]]:
    """Every lease row (active or closed) for a chunk: (lease_id, epoch, session_id, pid)."""
    with _audit_connection(runner_dir) as conn:
        rows = conn.execute(
            select(
                runner_schema.leases.c.lease_id,
                runner_schema.leases.c.epoch,
                runner_schema.leases.c.session_id,
                runner_schema.leases.c.pid,
            ).where(runner_schema.leases.c.chunk_id == chunk_id)
        ).all()
    return [(str(r[0]), int(r[1]), r[2], r[3]) for r in rows]


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
    git commit — the declaration, not the bare commit, is the durable
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
    """A graceful runner restart re-attaches to its in-flight session in place —
    same lease/epoch/session, only the pid rewritten, no retry consumed, the chunk lands
    once, and the drain-interrupted generation records a real (non-NULL) usage cost."""
    landed_file = "LANDED-restart-resume.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_sigint_drain_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # Let the chunk get claimed and the worker reach its commit, then hang mid-flight.
        assert wait_status(hub, chunk_id, {"running"}) == "running"
        _await_committed(runner_dir, chunk_id, landed_file)

        # Gracefully stop the runner: the shutdown hook marks the lease, then the drain
        # SIGINTs its worker group — `terminate` blocks until that's done, worker included.
        terminate(runner_proc)
        before = _leases_for_chunk(runner_dir, chunk_id)
        assert len(before) == 1, f"expected one lease before restart, got {before}"
        lease_id, epoch, session_id, pid_before = before[0]
        assert session_id and pid_before is not None
        assert _open_resume_intents(runner_dir) == {lease_id}, "graceful shutdown did not mark a resume-intent"
        _wait_pid_gone(pid_before, timeout=5)  # the drain's own SIGINT, not a leftover orphan

        # Restart: its first tick RESUMEs the session, recording the interrupted generation's
        # usage fact off the stdout file the SIGINT trap wrote before minting the new one.
        runner_proc = start_runner(runner_dir, crash_point=None)
        assert wait_status(hub, chunk_id, {"done"}) == "done", "chunk did not converge after graceful restart"

        # A real cost, parsed from the SIGINT trap's envelope — not the NULL-cost fallback.
        assert _usage_fact_cost(runner_dir, lease_id, 1) is not None, (
            "the SIGINT-interrupted generation recorded no cost — the drain's envelope was lost"
        )

        after = _leases_for_chunk(runner_dir, chunk_id)
        # Nothing worked twice: still exactly one lease, same lease/epoch/session — a same-lease
        # resume, not a retry (which would mint a new lease + epoch + session).
        assert len(after) == 1, f"restart-resume minted an extra lease (retry, not resume): {after}"
        r_lease_id, r_epoch, r_session_id, pid_after = after[0]
        assert (r_lease_id, r_epoch, r_session_id) == (lease_id, epoch, session_id)
        assert pid_after != pid_before, "the resumed process pid was not rewritten"
        # The intent was consumed by RESUME.
        assert _open_resume_intents(runner_dir) == set()

        _assert_invariants(runner_dir, hub_dir, when="after graceful restart-resume", after_recovery=True)
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- Ungraceful restart-resume — crash mid-work, no graceful marker ---


def _session_ends(runner_dir: Path) -> set[str]:
    store, engine = _runner_store(runner_dir)
    try:
        return store.session_ended_lease_ids()
    finally:
        engine.dispose()


def test_kill9_runner_resumes_in_flight_session(crash_env: CrashEnv, tmp_path: Path) -> None:
    """An involuntary ``kill -9`` mid-build (no graceful marker) still re-attaches the session
    — startup crash-recovery finds the killed-mid-work lease itself and routes it
    to the same RESUME the graceful path uses, landing the chunk exactly once."""
    landed_file = "LANDED-crash-resume.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(
            runner_dir, hub_dir, when="after ungraceful kill -9 of the runner mid-build", after_recovery=True
        )

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

        _assert_invariants(runner_dir, hub_dir, when="after ungraceful crash restart-resume", after_recovery=True)
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
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        # Restart UNARMED: RESUME recovers and the chunk converges — exactly once, still one lease.
        runner_proc = start_runner(runner_dir, crash_point=None)
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"

        after = _leases_for_chunk(runner_dir, chunk_id)
        # Same-lease resume across the crash: no extra lease minted (that would be a retry), and the
        # lease/epoch/session are the ones marked before the restart — the pid is the only rewrite.
        assert len(after) == 1, f"resume across a crash at {point} minted an extra lease (retry): {after}"
        assert (after[0][0], after[0][1], after[0][2]) == (lease_id, epoch, session_id)
        assert _wait_for_cleared_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- The same generic/resume windows, reached under an OpenCode lineage ---------------


def _ingest_opencode_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
    """:func:`_ingest_chunk`'s twin, minting :func:`opencode_graph_yaml` instead of
    :func:`graph_yaml` — an OpenCode-lineage ``build`` node opens the SAME
    harness-neutral ``spawn.*``/``advance.*`` windows under that lineage."""
    minted = hub.post("/api/graphs", json={"definition_yaml": opencode_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "an OpenCode crash-sweep chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


def _lease_harness_ids_for_chunk(runner_dir: Path, chunk_id: str) -> set[str | None]:
    """Every distinct ``harness_id`` recorded across ``chunk_id``'s leases — confirms a
    convergence landed under the lineage a scenario armed, not a silent fallback to
    another harness, regardless of whether the point's own recovery path re-mints a fresh
    lease (a generic point's retry) or resumes the same one (a resume point)."""
    with _audit_connection(runner_dir) as conn:
        rows = conn.execute(
            select(runner_schema.leases.c.harness_id).where(runner_schema.leases.c.chunk_id == chunk_id)
        ).all()
    assert rows, f"no leases recorded for chunk {chunk_id}"
    return {row[0] for row in rows}


@pytest.mark.parametrize("point", _OPENCODE_GENERIC_SWEEP)
def test_kill9_at_crash_point_under_opencode(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """:func:`test_kill9_at_crash_point`'s twin under an OpenCode lineage — the same
    ``spawn.*``/``advance.*`` windows a Claude-Code graph never reaches under OpenCode,
    armed here against :func:`opencode_graph_yaml`'s ``build`` node instead."""
    landed_file = f"LANDED-OPENCODE-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # Every named point here fires in the RUNNER's loop (`src/blizzard/runner/lifecycle/`), never the hub.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_opencode_chunk(hub, crash_env.forge, landed_file)

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=point)

        # Wait for the armed runner to reach its point and self-SIGKILL.
        code = wait_death(runner_proc)
        assert code == -9, (
            f"armed runner at {point} exited {code}, not SIGKILL (-9) under OpenCode; point never reached — "
            "harness selection may have silently fallen back to Claude Code"
        )

        _assert_invariants(
            runner_dir, hub_dir, when=f"immediately after OpenCode kill at {point}", after_recovery=False
        )

        runner_proc = start_runner(runner_dir, crash_point=None)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after OpenCode kill at {point} (last {status!r})"

        _assert_invariants(runner_dir, hub_dir, when=f"after OpenCode convergence past {point}", after_recovery=True)

        # Every lease recorded for this chunk, not just the last, must carry the OpenCode
        # harness — a silent fall-back to Claude Code would still land the file.
        assert _lease_harness_ids_for_chunk(runner_dir, chunk_id) == {OPENCODE_HARNESS_ID}, (
            f"a lease that converged past {point} was not recorded under the OpenCode harness"
        )

        # Exactly-once delivery: the file is reachable from bare main exactly once.
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


def _opencode_hanging_graph_yaml(landed_file: str) -> str:
    """:func:`_hanging_graph_yaml`'s twin under an OpenCode lineage: the same
    commit-then-``hang()`` ``build -> deliver`` shape, with ``build`` resuming
    :data:`OPENCODE_SESSION_NAME` so the session a restart re-attaches is OpenCode's own —
    what opens the `resume.wake.*`/`resume.after-*` windows under that lineage instead of
    Claude Code's."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "sessions": {OPENCODE_SESSION_NAME: {"harnesses": ["opencode"]}},
        "nodes": {
            "build": {
                "executor": "runner",
                "session": f"resume:{OPENCODE_SESSION_NAME}",
                "prompt": opencode_build_script(landed_file) + "hang()\n",
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
            "deliver": land_node(),
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_opencode_hanging_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
    """:func:`_ingest_hanging_chunk`'s twin, minting :func:`_opencode_hanging_graph_yaml`."""
    minted = hub.post("/api/graphs", json={"definition_yaml": _opencode_hanging_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "an OpenCode restart-resume chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


@pytest.mark.parametrize("point", _OPENCODE_RESUME_SWEEP)
def test_kill9_at_resume_crash_point_under_opencode(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """:func:`test_kill9_at_resume_crash_point`'s twin under an OpenCode lineage — a
    kill at a resume boundary still re-attaches exactly once, converging under the same
    lease/epoch/session and the same (OpenCode) harness."""
    landed_file = f"LANDED-OPENCODE-resume-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id = _ingest_opencode_hanging_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        # Let the OpenCode worker reach its commit and hang mid-flight, then gracefully stop
        # to mark the lease for restart-resume.
        assert wait_status(hub, chunk_id, {"running"}) == "running"
        _await_committed(runner_dir, chunk_id, landed_file)
        assert _lease_harness_ids_for_chunk(runner_dir, chunk_id) == {OPENCODE_HARNESS_ID}, (
            "the hanging build's own fresh mint was not dispatched to the OpenCode harness"
        )
        terminate(runner_proc)
        before = _leases_for_chunk(runner_dir, chunk_id)
        assert len(before) == 1, f"expected one lease before restart, got {before}"
        lease_id, epoch, session_id, _pid_before = before[0]
        assert _open_resume_intents(runner_dir) == {lease_id}, "graceful shutdown did not mark a resume-intent"

        # Restart ARMED at the resume boundary: the first tick's RESUME reaches it (against the
        # OpenCode session) and self-SIGKILLs.
        runner_proc = start_runner(runner_dir, crash_point=point)
        code = wait_death(runner_proc)
        assert code == -9, (
            f"armed runner at {point} exited {code}, not SIGKILL (-9) under OpenCode; point never reached?"
        )
        _assert_invariants(
            runner_dir, hub_dir, when=f"immediately after OpenCode kill at {point}", after_recovery=False
        )

        # Restart UNARMED: RESUME recovers and the chunk converges — exactly once, still one lease.
        runner_proc = start_runner(runner_dir, crash_point=None)
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after OpenCode kill at {point}"

        after = _leases_for_chunk(runner_dir, chunk_id)
        assert len(after) == 1, f"resume across an OpenCode crash at {point} minted an extra lease (retry): {after}"
        assert (after[0][0], after[0][1], after[0][2]) == (lease_id, epoch, session_id)
        assert _wait_for_cleared_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"
        # Convergence stayed on the OpenCode lineage the whole way through the crash — never a
        # silent re-mint under a different (e.g. default Claude Code) harness.
        assert _lease_harness_ids_for_chunk(runner_dir, chunk_id) == {OPENCODE_HARNESS_ID}, (
            f"convergence past {point} lost the OpenCode lineage — the resumed lease's harness changed"
        )
        _assert_invariants(runner_dir, hub_dir, when=f"after OpenCode convergence past {point}", after_recovery=True)
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- Live detach recovery — the abandon crash point ---


@scripted_build
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
            "deliver": land_node(),
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_abandon_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str, marker: Path) -> str:
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
    with _audit_connection(runner_dir) as conn:
        row = conn.execute(
            select(runner_schema.lease_closures.c.reason).where(runner_schema.lease_closures.c.lease_id == lease_id)
        ).first()
    return str(row[0]) if row is not None else None


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


def _wait_for_cleared_resume_intents(runner_dir: Path, *, timeout: float = 30.0) -> set[str]:
    """Poll until no resume-intent is open, or the timeout elapses (return whatever was last seen).

    Abandon, pause-park and preempt each write their resume-clear one statement *after* the
    closure, so a read taken the instant that closure lands can still legitimately see it open."""
    deadline = time.monotonic() + timeout
    intents = _open_resume_intents(runner_dir)
    while intents and time.monotonic() < deadline:
        time.sleep(0.25)
        intents = _open_resume_intents(runner_dir)
    return intents


@pytest.mark.parametrize("point", _ABANDON_SWEEP)
def test_kill9_at_abandon_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` anywhere inside the abandon — before its release, or after it and before
    the closure — recovers through restart-resume's path, not REAP's: the lease closes
    ``released``, and the chunk is re-claimable and lands exactly once."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    marker = tmp_path / "hang-once.marker"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)
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
        assert _wait_for_cleared_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"

        # Re-claimable: the same (only) runner picks the now-ready chunk back up fresh and, this
        # time past the marker, runs it to completion rather than hanging again.
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"
        after = _leases_for_chunk(runner_dir, chunk_id)
        assert len(after) == 2, f"expected the original (released) lease plus one fresh re-claim: {after}"
        lease_ids_after = {row[0] for row in after}
        assert lease_id_before in lease_ids_after
        fresh_lease_id = next(lid for lid in lease_ids_after if lid != lease_id_before)
        assert _closure_reason(runner_dir, fresh_lease_id) == "transitioned", "the fresh re-claim did not land cleanly"

        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- Operator chunk pause — the pause-park crash point ---


def _open_pause_parks(runner_dir: Path) -> set[str]:
    store, engine = _runner_store(runner_dir)
    try:
        return store.pause_parked_lease_ids()
    finally:
        engine.dispose()


@pytest.mark.parametrize("point", _PAUSE_SWEEP)
def test_kill9_at_pause_park_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` between a paused worker's interrupt and its durable park still keeps the
    claim — recovery parks the chunk rather than abandoning it, and the resumed
    session lands exactly once under the same lease, no retry consumed."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)
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
        assert _wait_for_cleared_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"
        _assert_invariants(
            runner_dir, hub_dir, when=f"after the pause-park recovered past {point}", after_recovery=True
        )

        # The operator resumes: the SAME session finishes the work it was paused mid-way through.
        resumed = hub.post(f"/api/chunks/{chunk_id}/resume", json={"by": "crash-sweep"})
        assert resumed.status_code == 202, resumed.text
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"

        after = _leases_for_chunk(runner_dir, chunk_id)
        # Nothing worked twice: still exactly one lease, same lease/epoch/session — the pause cost
        # the chunk a process, not an attempt (a retry would have minted a second lease).
        assert len(after) == 1, f"the pause/resume cycle minted an extra lease (retry, not resume): {after}"
        assert (after[0][0], after[0][1], after[0][2]) == (lease_id, epoch, session_id)
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- Usage-limit park — the usagelimit.* crash points ---


def _ingest_usage_limited_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
    """Mint the worker-side usage-limit graph and ingest a fresh issue against it to a ready
    chunk — the entry node's own generation exits usage-limited, never reaching a judge."""
    minted = hub.post("/api/graphs", json={"definition_yaml": usage_limited_graph_yaml(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a usage-limit crash chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


def _ingest_usage_limited_judge_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str, marker: Path) -> str:
    """Mint the judge-side usage-limit graph and ingest a fresh issue against it to a ready
    chunk — the worker commits and exits cleanly; its judge elicitation is the one that exits
    usage-limited, on its first call only (``marker`` gates the resumed elicitation's verdict)."""
    minted = hub.post("/api/graphs", json={"definition_yaml": usage_limited_judge_graph_yaml(landed_file, marker)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a usage-limit judge crash chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id


def _usage_limit_mark_count(runner_dir: Path) -> int:
    """Every mark on the subscription the runner spends."""
    with _audit_connection(runner_dir) as conn:
        return len(conn.execute(select(runner_schema.subscription_usage_limits.c.id)).all())


def _clear_subscription_floor(runner_dir: Path, port: int) -> None:
    """Open the floor of the subscription the runner spends through the host's clear route."""
    slug = load_runner_config(runner_dir).subscriptions[0].slug
    with httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as host:
        host.post(f"/api/host/subscriptions/{slug}/floor/clear", json={"by": "crash-sweep"}).raise_for_status()


@pytest.mark.parametrize("point", _USAGE_LIMIT_SWEEP)
def test_kill9_at_usage_limit_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` between the usage-limit mark and its durable park still keeps the claim —
    recovery replays the mark as a no-op and re-parks the lease rather than abandoning it, no
    brake is ever engaged, and the floor opening resumes the same lease in place, no retry
    consumed. Unlike the operator
    pause-park scenario, nothing external triggers this: the runner discovers the limit itself
    from the exited generation's own transcript, with no hub-side pause API call at all. Swept
    over both of the family's members: the worker-side window (an exited worker generation is
    the one classified) and the judge-side window (its judge elicitation is), each needing its
    own chunk shape since the two exits happen on different invocations."""
    is_judge = point.startswith("usagelimit.judge")
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    runner: httpx2.Client | None = None
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        if is_judge:
            marker = tmp_path / "judge-usage-limit-seen"
            chunk_id = _ingest_usage_limited_judge_chunk(hub, crash_env.forge, landed_file, marker)
        else:
            chunk_id = _ingest_usage_limited_chunk(hub, crash_env.forge, landed_file)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        # Armed from the start: unarmed in effect until the runner's own ADVANCE step
        # classifies the exited generation and reaches `point` inside the park it triggers.
        runner_proc = start_runner(runner_dir, crash_point=point)

        assert wait_status(hub, chunk_id, {"running"}) == "running"
        if is_judge:
            # The worker's own commit must land before the judge elicitation that follows it
            # is the one racing the crash point.
            _await_committed(runner_dir, chunk_id, landed_file)
        before = _lease_for_chunk(runner_dir, chunk_id)
        lease_id, epoch, session_id, _pid_before = before

        # The armed runner classifies the usage-limited exit, marks the limit on the subscription it
        # spends, and self-SIGKILLs before the park is durable — no external pause call at all.
        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)
        assert _open_pause_parks(runner_dir) == set(), "the park was durable — the crash point fired too late"

        # Restart UNARMED: recovery must re-run the park, NOT abandon the chunk.
        runner_proc = start_runner(runner_dir, crash_point=None)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and _open_pause_parks(runner_dir) != {lease_id}:
            time.sleep(0.25)
        assert _open_pause_parks(runner_dir) == {lease_id}, (
            "the usage-limited lease was never re-parked after the crash — recovery abandoned "
            "the chunk instead of keeping the claim"
        )
        # The claim survived the crash: no closure at all.
        assert _closure_reason(runner_dir, lease_id) is None, (
            "recovery closed the usage-limited lease — the pause became a detach/failure"
        )
        # A usage-limit park, not a hub-side per-chunk pause — the hub still sees the chunk as
        # running, unlike the operator pause-park scenario's `"paused"`.
        assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"
        await_host(runner_port, runner_proc)
        runner = runner_api(runner_dir, runner_port)
        view = runner.get("/api/runner").json()
        assert view["pause"]["local"] is False, "a usage limit engaged the runner's brake"
        assert _usage_limit_mark_count(runner_dir) == 1, "the recovered pass marked the limit a second time"
        _assert_invariants(
            runner_dir, hub_dir, when=f"after the usage-limit park recovered past {point}", after_recovery=True
        )

        # The limit named no reset, so the floor would hold for its fallback; a clear opens it now and
        # the SAME session finishes the work it was parked mid-way through.
        _clear_subscription_floor(runner_dir, runner_port)
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"

        after = _leases_for_chunk(runner_dir, chunk_id)
        # Nothing worked twice: still exactly one lease, same lease/epoch/session — the park
        # cost the chunk a process, not an attempt.
        assert len(after) == 1, f"the usage-limit park/resume cycle minted an extra lease (retry, not resume): {after}"
        assert (after[0][0], after[0][1], after[0][2]) == (lease_id, epoch, session_id)
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        if runner is not None:
            runner.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- Release of a gate-parked chunk — the release.* crash points ---

#: The utilization line the between-steps scenario's runner works under, and a reading on each side of it.
_RELEASE_LINE_PCT = 95.0
_UNDER_LINE_PCT = 40.0
_OVER_LINE_PCT = 96.0


_JUDGEMENT_SCRIPT_PASS = "verdict('pass', 'the mock harness committed the change; checks are green')\n"


def _wait_then_build_script(landed_file: str, go_marker: Path) -> str:
    """:func:`build_script`, held until ``go_marker`` exists — so a sample crossing the line lands while the step is
    in flight, and the gate declines the node after it, never this one."""
    return (
        f"import pathlib, time\nwhile not pathlib.Path({str(go_marker)!r}).exists():\n    time.sleep(0.2)\n"
    ) + build_script(landed_file)


def _usage_limited_once_build_script(landed_file: str, marker: Path) -> str:
    """Usage-limited on its first call, before touching the repo; :func:`build_script` on every later one — so the
    chunk a usage limit gave back is built from its step's start once it is claimed again."""
    first = (
        "import pathlib\n"
        f"_marker = pathlib.Path({str(marker)!r})\n"
        "if not _marker.exists():\n"
        "    _marker.write_text('limited once\\n')\n"
        "    usage_limited()\n"
        "else:\n"
    )
    return first + "".join(f"    {line}\n" for line in build_script(landed_file).splitlines())


def _release_graph_yaml(build_prompt: str, *, review: bool) -> str:
    """A ``build -> [review ->] deliver`` graph; ``review`` gives the step the gate declines a node to enter."""
    import yaml

    judged = {
        "prompt": _JUDGEMENT_SCRIPT_PASS,
        "choices": {"pass": {"description": "Committed and green.", "to": "review" if review else "deliver"}},
    }
    nodes: dict[str, object] = {
        "build": {
            "executor": "runner",
            "prompt": build_prompt,
            "judgement": judged,
            "retries": {"max": 1, "exhausted": "escalate"},
        }
    }
    if review:
        nodes["review"] = {
            "executor": "runner",
            "prompt": "print('reviewed')\n",
            "judgement": {
                "prompt": _JUDGEMENT_SCRIPT_PASS,
                "choices": {"pass": {"description": "Ready to land.", "to": "deliver"}},
            },
            "retries": {"max": 1, "exhausted": "escalate"},
        }
    nodes["deliver"] = land_node()
    return yaml.safe_dump({"name": "default-delivery", "entry": "build", "nodes": nodes}, sort_keys=False)


def _ingest_release_chunk(hub: httpx2.Client, forge: httpx2.Client, title: str, graph: str) -> str:
    minted = hub.post("/api/graphs", json={"definition_yaml": graph})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "a release crash chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def _utilization_sample(runner_dir: Path, utilization_pct: float) -> None:
    """A successful sample of the runner's subscription taken now, its one window at ``utilization_pct``."""
    config = load_runner_config(runner_dir)
    window = {
        "window": "five_hour",
        "utilization_pct": utilization_pct,
        "resets_at": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
        "window_seconds": 5 * 3600,
    }
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(runner_schema.external_usage_samples).values(
                    slug=config.subscriptions[0].slug,
                    sampled_at=datetime.now(UTC),
                    payload=json.dumps({"windows": [window]}),
                )
            )
    finally:
        engine.dispose()


def _holds_environments(runner_dir: Path, chunk_id: str) -> bool:
    store, engine = _runner_store(runner_dir)
    try:
        return chunk_id in store.live_tenure_chunk_ids()
    finally:
        engine.dispose()


def _spawn_usage_facts(runner_dir: Path, lease_id: str) -> int:
    with _audit_connection(runner_dir) as conn:
        rows = conn.execute(
            select(runner_schema.usage_facts.c.id)
            .where(runner_schema.usage_facts.c.lease_id == lease_id)
            .where(runner_schema.usage_facts.c.kind.in_(("spawn", "resume")))
        ).all()
    return len(rows)


@pytest.mark.parametrize("point", _RELEASE_SWEEP)
def test_kill9_at_release_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` after the hub took a gate-parked chunk back, before the runner let go of it, converges: no lease,
    park, or environment kept that the hub no longer routes here, and the chunk lands once, cold, when its gate reopens."""
    usage_limited = point.startswith("release.usage-limited")
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    marker = tmp_path / "release-marker"

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        if usage_limited:
            graph = _release_graph_yaml(_usage_limited_once_build_script(landed_file, marker), review=False)
            throttle = ThrottleSettings(on_usage_limit=HoldOrRelease.RELEASE)
        else:
            graph = _release_graph_yaml(_wait_then_build_script(landed_file, marker), review=True)
            throttle = ThrottleSettings(
                utilization=UtilizationSettings(below_pct=_RELEASE_LINE_PCT), on_decline=HoldOrRelease.RELEASE
            )
        chunk_id = _ingest_release_chunk(hub, crash_env.forge, landed_file, graph)
        config = write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        with_runner(config, throttle=throttle)
        _utilization_sample(runner_dir, _UNDER_LINE_PCT)
        runner_proc = start_runner(runner_dir, crash_point=point)

        assert wait_status(hub, chunk_id, {"running"}) == "running"
        lease_id, _epoch, _session, _pid = _lease_for_chunk(runner_dir, chunk_id)
        if not usage_limited:
            # The line is crossed while the first step runs; that step finishes, and its next node is declined.
            _utilization_sample(runner_dir, _OVER_LINE_PCT)
            marker.write_text("go\n")

        code = wait_death(runner_proc)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"
        assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready", "the hub never took the chunk back"
        assert _holds_environments(runner_dir, chunk_id), "the environments were let go before the crash point"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        # Restart UNARMED, the gate still declining: a usage-limited lease is abandoned at once, and an interrupted
        # claim's binding is held unclaimed until the gate admits again; either way the chunk stays ready.
        runner_proc = start_runner(runner_dir, crash_point=None)
        await_host(runner_port, runner_proc)
        if usage_limited:
            assert _wait_for_closure(runner_dir, lease_id) == "released"
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline and _holds_environments(runner_dir, chunk_id):
                time.sleep(0.25)
            assert not _holds_environments(runner_dir, chunk_id), f"recovery past {point} kept the environments"
            assert _spawn_usage_facts(runner_dir, lease_id) == 1, "the half-done step's usage was not booked once"
        else:
            time.sleep(5.0)
            assert _closure_reason(runner_dir, lease_id) is not None, "the finished step's lease never closed"
        assert _open_pause_parks(runner_dir) == set(), "a park outlived the release"
        assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready", "the declining runner claimed it back"
        assert len(_leases_for_chunk(runner_dir, chunk_id)) == 1, "a node was entered while the gate declined"
        _assert_invariants(runner_dir, hub_dir, when=f"after recovery past {point}", after_recovery=True)

        # The gate reopens: the chunk is claimed cold and lands once.
        if usage_limited:
            _clear_subscription_floor(runner_dir, runner_port)
        else:
            _utilization_sample(runner_dir, _UNDER_LINE_PCT)
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"
        # Two leases and no more: the abandoned build and its cold re-entry, or one per runner node.
        assert len(_leases_for_chunk(runner_dir, chunk_id)) == 2
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- Generic hub command node (#65) — the hubnode.* per-step crash windows ---


# The ``merge`` node's ``land`` step: merges each submitted branch to base by pinned SHA against the
# mock forge; idempotent by construction, and refuses an empty delivery as ``land`` does.
_LAND_STEP = """python3 - <<'PYEOF'
import json, os, sys, urllib.error, urllib.request

forge = os.environ.get("BZ_FORGE_URL", "")
base = os.environ.get("BZ_HUB_BASE_BRANCH", "main")
commits = json.loads(os.environ.get("BZ_HUB_GIT_COMMITS") or "[]")

if not commits and os.environ.get("BZ_HUB_EXPECT_GIT_COMMITS", "1") != "0":
    print(
        "no git commits to deliver: this chunk submitted no git_commit artifact, so there "
        "is nothing to open a PR for.",
        file=sys.stderr,
    )
    sys.exit(1)


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


def _ingest_hub_command_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
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
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                hub_scoped(
                    select(hub_schema.hub_exec_slot.c.slot_id).where(hub_schema.hub_exec_slot.c.released_at.is_(None))
                )
            ).all()
        return len(rows)
    finally:
        engine.dispose()


def _count_pulls(forge: httpx2.Client) -> int:
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
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        # Restart the hub UNARMED: the runner re-flushes the build completion, whose idempotent
        # replay re-enters the hub-node branch and resumes the interrupted run to completion.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)

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
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference

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


def _ingest_pending_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
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
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        # Restart the hub UNARMED: pending-ness is derived from the durable poll fact, so
        # the chunk just resumes polling and lands.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)

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

# A window the per-step `hubnode.*` registry points cannot reach: `src/blizzard/hub/graphs/scripts/land_default.py`
# loops over repos inside ONE `run:` step, marking each through a mid-run callback instead.

_WEB_REPO_NAME = "toy-web"
_LAND_STEP_COMMAND = "python3 -m blizzard.hub.graphs.scripts.land_default"


def _two_repo_build_script(landed_file: str) -> str:
    """A build node that commits ``landed_file`` in BOTH fixture repos' worktrees, then
    pushes and declares each through the real `blizzard runner artifact commit` verb.
    So the chunk submits a ``git_commit`` pointer for ``toy-api`` AND
    ``toy-web`` — a genuine 2-repo land for the deliver script to loop over."""
    return (
        "import subprocess, pathlib\n"
        f"for repo in [{REPO_NAME!r}, {_WEB_REPO_NAME!r}]:\n"
        # The basic provider's worktrees arrive detached; the worker owns its branch.
        f'    subprocess.run(["git", "-C", repo, "switch", "-C", {BUILD_BRANCH!r}], check=True)\n'
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
    the REAL packaged ``src/blizzard/hub/graphs/scripts/land_default.py`` script (not the sweep's ``true`` stand-in),
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
                        "failure": {"description": "The land script failed; back to build.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _merged_markers(hub: httpx2.Client, chunk_id: str) -> list[str]:
    """The chunk's durable ``merged/<repo>`` marker artifact names, read through the hub API."""
    detail = hub.get(f"/api/chunks/{chunk_id}")
    detail.raise_for_status()
    return sorted(a["name"] for a in detail.json()["artifacts"] if a["name"].startswith("merged/"))


def _create_web_repository(hub: httpx2.Client, forge_port: int) -> None:
    """The second repository the two-repo land resolves its ``toy-web`` commit against."""
    create_repositories(hub, [fixture_repository(_WEB_REPO_NAME, f"http://127.0.0.1:{forge_port}", owner=OWNER)])


def _repo_pull_count(forge: httpx2.Client, repo: str) -> int:
    resp = forge.get(f"/repos/{OWNER}/{repo}/pulls", params={"state": "all"})
    resp.raise_for_status()
    return len(resp.json())


# --- Hub action pass — the action.* effect-write windows ---


def _action_graph_yaml(landed_file: str) -> str:
    """A ``build -> deliver(action) -> done`` graph whose ``deliver`` takes ``test.sequence``: each pass records
    a ``test-sequence/<n>`` marker through the effect sink, then answers ``done``."""
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
                    "choices": {"pass": {"description": "Committed and green.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "action": "test.sequence",
                "with": {"choices": ["done"]},
                "judgement": {
                    "choices": {
                        "done": {"description": "Delivered; finish.", "to": "done"},
                        "failure": {"description": "The pass failed; back to build.", "to": "build"},
                    },
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


@pytest.mark.parametrize("point", _ACTION_SWEEP)
def test_kill9_at_action_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` while a pass's effect is written recovers: a crash before the effect is durable re-takes
    the pass and writes it once, a crash after it finds it durable and the next pass counts past it, and no
    hub-execution slot leaks."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        minted = hub.post("/api/graphs", json={"definition_yaml": _action_graph_yaml(landed_file)})
        assert minted.status_code == 201, minted.text
        issue = crash_env.forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "an action sweep"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        code = wait_death(hub_proc)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        # Restart unarmed: the runner re-flushes the build completion, whose replay re-drives the hub node.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        assert _live_exec_slots(hub_dir) == 0, f"a hub_exec_slot leaked live after convergence past {point}"

        names = [a["name"] for a in hub.get(f"/api/chunks/{chunk_id}").json()["artifacts"]]
        recorded = sorted(n for n in names if n.startswith("test-sequence/"))
        if point == "action.after-effect.before-write":
            assert recorded == ["test-sequence/0"], (
                f"the unwritten effect must be written once on the re-take: {recorded}"
            )
        elif point == "action.after-write.before-resume":
            assert recorded == ["test-sequence/0", "test-sequence/1"], f"the re-take must count past it: {recorded}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- A two-repository ``land`` — the action.* windows, and the window between its repositories ---

# The first landing record's own write: durable beside its close intents, before the pass resumes into the
# second repository's merge — the window between the two repositories.
_BETWEEN_REPOSITORIES_POINT = "close.after-enqueue.before-drain"


def _land_graph_two_repo_yaml(landed_file: str) -> str:
    """A ``build -> deliver`` graph whose ``deliver`` takes the shipped ``land`` action over both fixture
    repositories against the mock forge, with every answer routed and a brisk poll cadence."""
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
                "action": "land",
                "poll_interval": 1,
                "poll_timeout": 90,
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo's PR read dirty; back to build.", "to": "build"},
                        "failure": {"description": "A repo could not land; back to build.", "to": "build"},
                        "inherited-failure": {
                            "description": "Every remaining check failure is inherited from the base; back to build.",
                            "to": "build",
                        },
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _landed_repos(hub_dir: Path, chunk_id: str) -> list[str]:
    """The repositories a chunk's landing records name, read straight off the hub store while the hub is down."""
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        with engine.connect() as conn:
            landed = hub_schema.delivery_repo_landed
            rows = conn.execute(hub_scoped(select(landed.c.repo).where(landed.c.chunk_id == chunk_id))).all()
        return sorted(row.repo for row in rows)
    finally:
        engine.dispose()


def _kill_two_repo_land(crash_env: CrashEnv, tmp_path: Path, point: str, *, merged_at_kill: int) -> None:
    """Drive a two-repository ``land`` into ``point``, assert ``merged_at_kill`` landings were durable when the hub
    died, restart it unarmed, and assert each repository landed exactly once through exactly one PR."""
    landed_file = f"LANDED-{point.replace('.', '_')}.md"
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    api_bare = crash_env.origins / f"{REPO_NAME}.git"
    web_bare = crash_env.origins / f"{_WEB_REPO_NAME}.git"

    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        _create_web_repository(hub, crash_env.forge_port)
        api_pulls_before = _repo_pull_count(crash_env.forge, REPO_NAME)
        web_pulls_before = _repo_pull_count(crash_env.forge, _WEB_REPO_NAME)

        minted = hub.post("/api/graphs", json={"definition_yaml": _land_graph_two_repo_yaml(landed_file)})
        assert minted.status_code == 201, minted.text
        issue = crash_env.forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": "a land sweep"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        code = wait_death(hub_proc)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)
        landed_at_kill = _landed_repos(hub_dir, chunk_id)
        assert len(landed_at_kill) == merged_at_kill, f"{point}: {landed_at_kill} were durable at the kill"

        # Restart unarmed: the runner re-flushes the build completion, whose replay re-drives the hub node.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"}, timeout=120.0)
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        assert _live_exec_slots(hub_dir) == 0, f"a hub_exec_slot leaked live after convergence past {point}"

        assert _merged_markers(hub, chunk_id) == sorted(
            [f"merged/{OWNER}/{REPO_NAME}", f"merged/{OWNER}/{_WEB_REPO_NAME}"]
        )
        for bare in (api_bare, web_bare):
            tree = git_bare(bare, "log", "--oneline", "--", landed_file)
            landings = [ln for ln in tree.splitlines() if ln.strip()]
            assert len(landings) == 1, f"{landed_file} landed {len(landings)}x on {bare.name}:\n{tree}"
        assert _repo_pull_count(crash_env.forge, REPO_NAME) - api_pulls_before == 1, "toy-api opened != 1 PR"
        assert _repo_pull_count(crash_env.forge, _WEB_REPO_NAME) - web_pulls_before == 1, "toy-web opened != 1 PR"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


@pytest.mark.parametrize("point", _ACTION_SWEEP)
def test_kill9_at_action_crash_point_through_land(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` at a ``land`` pass's first effect write — the first repository's PR identity, before any
    merge — re-takes the pass on restart against the same PRs, and each repository lands exactly once."""
    _kill_two_repo_land(crash_env, tmp_path, point, merged_at_kill=0)


def test_kill9_between_land_repositories(crash_env: CrashEnv, tmp_path: Path) -> None:
    """A two-repository ``land`` killed between its repositories — the first landing record durable, the second
    repository not yet merged — lands the second on restart without merging the first twice."""
    _kill_two_repo_land(crash_env, tmp_path, _BETWEEN_REPOSITORIES_POINT, merged_at_kill=1)


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
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        _create_web_repository(hub, crash_env.forge_port)
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
        _assert_invariants(runner_dir, hub_dir, when="immediately after mid-script kill -9", after_recovery=False)

        # Restart the hub UNARMED (no pause env): the runner re-flushes the build completion,
        # land_default re-runs, skips the marked repo, and pushes only the unmarked one.
        hub_proc = start_hub(
            hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, new_session=True
        )
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"}, timeout=120.0)
        assert status == "done", f"chunk did not converge to done after the mid-script kill (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when="after convergence past the mid-script kill", after_recovery=True)

        # Both markers are now durable, and no live exec slot leaked.
        assert _merged_markers(hub, chunk_id) == sorted(
            [f"merged/{OWNER}/{REPO_NAME}", f"merged/{OWNER}/{_WEB_REPO_NAME}"]
        )
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


# --- Mid-script inter-repo-merge crash for the PR + CI-watch lane — `land_pr_ci`'s own
#     window, covering every lane's fast-forward-free delivery script ---

_LAND_PR_CI_STEP_COMMAND = "python3 -m blizzard.hub.graphs.scripts.land_pr_ci"


def _pr_ci_graph_two_repo_yaml(landed_file: str) -> str:
    """:func:`_default_graph_two_repo_yaml`'s twin for the PR + CI-watch lane: ``deliver``
    runs the REAL packaged ``src/blizzard/hub/graphs/scripts/land_pr_ci.py`` against the rebase-capable mock forge,
    with its full outcome set and a poll cadence wired, mirroring the shipped
    ``adv-dwf``/``bas-dwf`` graphs' ``deliver`` node."""
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
                "run": [{"name": "land-every-repo", "command": _LAND_PR_CI_STEP_COMMAND}],
                "poll_interval": 1,
                "poll_timeout": 90,
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo's PR read dirty; back to build.", "to": "build"},
                        "failure": {
                            "description": "A repo's check run failed, or the land script crashed; back to build.",
                            "to": "build",
                        },
                        "inherited-failure": {
                            "description": "Every remaining check failure is inherited from the base; back to build.",
                            "to": "build",
                        },
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def test_kill9_between_pr_ci_graph_repo_pushes(crash_env: CrashEnv, tmp_path: Path) -> None:
    """A ``kill -9`` between two repos' merges in the real ``land_pr_ci`` re-runs only the
    unmarked repo — the marked one is skipped by its durable marker, never re-looked-up —
    and lands each exactly once with no leaked ``hub:one-live-exec-slot``."""
    landed_file = "LANDED-mid-script-pr-ci-sweep.md"
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
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        _create_web_repository(hub, crash_env.forge_port)
        api_pulls_before = _repo_pull_count(crash_env.forge, REPO_NAME)
        web_pulls_before = _repo_pull_count(crash_env.forge, _WEB_REPO_NAME)

        minted = hub.post("/api/graphs", json={"definition_yaml": _pr_ci_graph_two_repo_yaml(landed_file)})
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
        _assert_invariants(
            runner_dir, hub_dir, when="immediately after mid-script kill -9 (land_pr_ci)", after_recovery=False
        )

        # Restart the hub UNARMED: land_pr_ci re-runs, skips the marked repo (marker
        # already durable) rather than looking its PR up again, and merges only the other.
        hub_proc = start_hub(
            hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, new_session=True
        )
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"}, timeout=120.0)
        assert status == "done", f"chunk did not converge to done after the mid-script kill (last {status!r})"
        _assert_invariants(
            runner_dir, hub_dir, when="after convergence past the mid-script kill (land_pr_ci)", after_recovery=True
        )

        # Both markers are now durable, and no live exec slot leaked.
        assert _merged_markers(hub, chunk_id) == sorted(
            [f"merged/{OWNER}/{REPO_NAME}", f"merged/{OWNER}/{_WEB_REPO_NAME}"]
        )
        assert _live_exec_slots(hub_dir) == 0, "a hub_exec_slot leaked live after the mid-script recovery (land_pr_ci)"

        # Exactly-once: each repo's change is reachable from its bare main exactly once.
        for bare in (api_bare, web_bare):
            tree = git_bare(bare, "log", "--oneline", "--", landed_file)
            landings = [ln for ln in tree.splitlines() if ln.strip()]
            assert len(landings) == 1, f"{landed_file} landed {len(landings)}x on {bare.name}:\n{tree}"

        # The marked repo was NOT re-merged as a duplicate PR: each repo opened exactly
        # one PR across the whole run, crash and recovery included.
        assert _repo_pull_count(crash_env.forge, REPO_NAME) - api_pulls_before == 1, "toy-api opened != 1 PR"
        assert _repo_pull_count(crash_env.forge, _WEB_REPO_NAME) - web_pulls_before == 1, "toy-web opened != 1 PR"
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
            "deliver": land_node(),
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest_preempt_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str, marker: Path) -> str:
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
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
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
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)
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
        assert _wait_for_cleared_resume_intents(runner_dir) == set(), "the resume-intent was not cleared after recovery"

        # The claim was kept throughout: the same runner re-enters and finishes the work.
        assert wait_status(hub, chunk_id, {"done"}) == "done", f"chunk did not converge after kill at {point}"
        after = _leases_for_chunk(runner_dir, chunk_id)
        reasons = sorted(filter(None, (_closure_reason(runner_dir, row[0]) for row in after)))
        assert reasons == ["preempted", "transitioned"], f"unexpected closure set after the restart: {reasons}"

        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        tree = git_bare(crash_env.origins / "toy-api.git", "log", "--oneline", "--", landed_file)
        commits = [line for line in tree.splitlines() if line.strip()]
        assert len(commits) == 1, f"{landed_file} landed {len(commits)} times on bare main:\n{tree}"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- The close-intent outbox — driven through the built-in `hub` work source, whose closer is ---
# --- local; `land` against the mock forge writes the landing marker that owes the close intent ---


def _close_intent_graph_yaml(landed_file: str) -> str:
    """``default-delivery``: ``build`` commits ``landed_file``, then ``land``, whose ``merged/<repo>`` marker owes
    the item's close intent — so both ``close.*`` windows open only after the runner claims ``build``."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build_script(landed_file),
                "produces": [{"name": "commit", "kind": "git_commit"}],
                "judgement": {
                    "prompt": "verdict('pass', 'the mock harness committed the change; checks are green')\n",
                    "choices": {"pass": {"description": "Committed; hand off to land.", "to": "land"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "land": land_node(),
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _no_op_graph_yaml() -> str:
    """``default-delivery``: a no-op ``build``, then a hub node taking ``pass`` — a chunk that converges, no forge."""
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
                "action": "pass",
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


def _ingest_hub_item_chunk(hub: httpx2.Client, definition_yaml: str) -> tuple[str, str]:
    """Mint ``definition_yaml`` as the packaged default's own name, then create a hub-owned work item — no
    forge issue. Returns ``(chunk_id, ref)``."""
    minted = hub.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert minted.status_code == 201, minted.text
    created = hub.post("/api/work-sources/hub/items", json={"title": "hub-item crash chunk", "body": "b"})
    assert created.status_code == 201, created.text
    body = created.json()
    chunk_id, ref = body["chunk_id"], body["ref"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    return chunk_id, ref


def _wait_item_delivered(hub: httpx2.Client, ref: str, *, timeout: float) -> dict | None:
    """Poll the hub-owned item until its closure is durable — the drain sweep's own
    cadence, not a request/response round trip (unconditional, no config knob to
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
    """A ``kill -9`` inside the close-intent outbox's own windows still
    converges: the pending intent survives the crash and the item closes exactly once,
    driven through the built-in ``hub`` work source, landed through ``land`` against the mock forge."""
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    # Both close.* windows fire inside the HUB. The item comes from the always-seated built-in `hub`
    # source; the seeded forge repository is what `land` merges through.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id, ref = _ingest_hub_item_chunk(hub, _close_intent_graph_yaml(f"close-{point}.txt"))

        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        # The runner's own poll loop is what drives the held chunk's hub-advance calls
        # (#65/#66) — no work for it to claim otherwise, since `land` is hub-executed.
        runner_proc = start_runner(runner_dir, crash_point=None)

        # Unlike every other family here, this crash point fires inside a periodically
        # jittered sweep (`Sweep.all`), not a synchronous request: its first pass runs
        # before the intent exists, so reaching the point can wait up to a full
        # `CLOSE_DRAIN_INTERVAL_SECONDS` for the jittered second pass. The default
        # `wait_death` timeout equals that interval exactly, racing its own upper bound.
        code = wait_death(hub_proc, timeout=CLOSE_DRAIN_INTERVAL_SECONDS + 60.0)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        # Restart the hub UNARMED; the runner's replayed hub-advance poll resumes the
        # interrupted run, and the always-on drain sweep retires the surviving intent.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)

        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)

        item = _wait_item_delivered(hub, ref, timeout=60.0)
        assert item is not None and item.get("closure") == "delivered", (
            f"item {ref} did not close after kill at {point} (last read: {item!r})"
        )

        # Exactly-once: the drain never double-closes a ref it already retired.
        hub_engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
        with hub_engine.connect() as conn:
            outcomes = conn.execute(
                hub_scoped(
                    select(hub_schema.work_item_closures.c.outcome).where(
                        (hub_schema.work_item_closures.c.chunk_id == chunk_id)
                        & (hub_schema.work_item_closures.c.source == "hub")
                        & (hub_schema.work_item_closures.c.ref == ref)
                        & (hub_schema.work_item_closures.c.outcome.in_(["closed", "gone"]))
                    )
                )
            ).all()
        assert len(outcomes) == 1, f"ref {ref} carries {len(outcomes)} terminal closure outcomes, not exactly one"
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


# --- The trace export sweep — the hub tells closed steps to an in-test OTLP sink ---

#: Every closed step is exported on the next pass, and a pass runs every second.
_TRACE_SWEEP_SECONDS = 1


def _scaffold_traced_hub(hub_dir: Path) -> None:
    """Scaffold the hub with no configured work source and the trace sweep tuned to a fast,
    zero-settle cadence — its ``[tracing]`` knobs set before the hub ever starts."""
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    toml = hub_dir / "blizzard-hub.toml"
    text = toml.read_text()
    for commented, live in (
        ("# sweep_seconds = 60", f"sweep_seconds = {_TRACE_SWEEP_SECONDS}"),
        ("# settle_seconds = 300", "settle_seconds = 0"),
    ):
        assert commented in text, f"scaffolded hub config no longer renders {commented!r}"
        text = text.replace(commented, live, 1)
    toml.write_text(text)


def _trace_cursor_rows(hub_dir: Path) -> list[tuple[int, int]]:
    """Every ``trace_cursor`` row as ``(id, span_count)``, oldest first."""
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                hub_scoped(
                    select(hub_schema.trace_cursor.c.id, hub_schema.trace_cursor.c.span_count).order_by(
                        hub_schema.trace_cursor.c.id
                    )
                )
            ).all()
    finally:
        engine.dispose()
    return [(row.id, row.span_count) for row in rows]


def _wait_requests(sink: OtlpSink, count: int, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(sink.requests) < count:
        time.sleep(0.25)


@pytest.mark.parametrize("point", _TRACE_SWEEP)
def test_kill9_at_trace_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` after the exporter accepted a batch but before its cursor row lands
    re-sends the same span ids on the next pass, and one cursor row then covers them."""
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    _scaffold_traced_hub(hub_dir)

    with otlp_sink() as sink:
        # Tracing is on from the hub's first start, before any step closes: the cursor
        # opens at enable time, so a step closed earlier would never be told.
        otel_env = {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": sink.traces_endpoint}
        hub_proc = start_hub(
            hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point, extra_env=otel_env
        )
        runner_proc = None
        hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
        try:
            await_http(hub, "/api/health", proc=hub_proc)
            chunk_id, _ = _ingest_hub_item_chunk(hub, _no_op_graph_yaml())
            write_runner_config(
                runner_dir,
                workspace=crash_env.workspace,
                bin_dir=crash_env.bin_dir,
                hub_port=hub_port,
                port=runner_port,
            )
            runner_proc = start_runner(runner_dir, crash_point=None)

            code = wait_death(hub_proc, timeout=90.0)
            assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
            _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

            killed_requests = len(sink.requests)
            assert killed_requests == 1, f"expected one accepted export before the kill, saw {killed_requests}"
            told_once = [span.span_id for span in sink.spans()]
            assert told_once, "the export accepted before the kill carried no spans"
            assert all(count == 0 for _, count in _trace_cursor_rows(hub_dir)), (
                "a cursor row recorded spans the killed pass never got to record"
            )

            hub_proc = start_hub(
                hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, extra_env=otel_env
            )
            await_http(hub, "/api/health", proc=hub_proc)
            status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
            assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
            _wait_requests(sink, killed_requests + 1, timeout=30.0)
            # Let any step that closed after the re-send be told too, so every cursor row is final.
            time.sleep(_TRACE_SWEEP_SECONDS * 3)
            _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        finally:
            hub.close()
            terminate(runner_proc)
            terminate(hub_proc)

        resent = [
            span.span_id
            for request in sink.requests[killed_requests : killed_requests + 1]
            for rs in request.resource_spans
            for ss in rs.scope_spans
            for span in ss.spans
        ]
        all_ids = [span.span_id for span in sink.spans()]
        twice = {span_id: all_ids.count(span_id) for span_id in told_once}
        assert all(count == 2 for count in twice.values()), f"span ids not told exactly twice: {twice}"
        assert set(told_once) <= set(resent), "the first pass after restart did not re-send the killed batch"

        advancing = [count for _, count in _trace_cursor_rows(hub_dir) if count > 0]
        assert advancing, "no cursor row recorded the re-sent batch"
        assert advancing[0] == len(resent), (
            f"the first advancing cursor row covers {advancing[0]} spans, not the re-sent {len(resent)}"
        )
        assert sum(advancing) == len(sink.spans()) - len(told_once), (
            "cursor rows do not cover each post-restart span exactly once"
        )


# --- The egress sweep — the hub writes closed steps and usage as files in a real directory ---

#: A pass runs every second.
_EGRESS_SWEEP_SECONDS = 1


def _scaffold_egress_hub(hub_dir: Path, directory: Path) -> None:
    """Scaffold the hub with no work source and the egress sweep on a fast, zero-settle cadence."""
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    directory.mkdir()
    toml = hub_dir / "blizzard-hub.toml"
    head, marker, tail = toml.read_text().partition("[egress]\n")
    assert marker, "scaffolded hub config no longer renders [egress]"
    for commented, live in (
        ('# directory = "/var/lib/blizzard/egress"', f'directory = "{directory}"'),
        ("# sweep_seconds = 60", f"sweep_seconds = {_EGRESS_SWEEP_SECONDS}"),
        ("# settle_seconds = 300", "settle_seconds = 0"),
        ("# min_free_bytes = 1073741824", "min_free_bytes = 0"),
    ):
        assert commented in tail, f"scaffolded hub config no longer renders {commented!r}"
        tail = tail.replace(commented, live, 1)
    toml.write_text(head + marker + tail)


def _egress_cursor_rows(hub_dir: Path) -> list[tuple[str, int, list[str]]]:
    """Every ``egress_cursor`` row as ``(dataset, row_count, files)``, oldest first."""
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                hub_scoped(
                    select(
                        hub_schema.egress_cursor.c.dataset,
                        hub_schema.egress_cursor.c.row_count,
                        hub_schema.egress_cursor.c.files,
                    ).order_by(hub_schema.egress_cursor.c.id)
                )
            ).all()
    finally:
        engine.dispose()
    return [(row.dataset, row.row_count, json.loads(row.files)) for row in rows]


def _egress_data_files(directory: Path) -> dict[str, list[dict[str, object]]]:
    """Every placed data file's rows, by its path relative to ``directory``."""
    placed: dict[str, list[dict[str, object]]] = {}
    for path in sorted(directory.glob("*/v1/date=*/*.ndjson.gz")):
        with gzip.open(path, "rt") as handle:
            placed[path.relative_to(directory).as_posix()] = [json.loads(line) for line in handle.read().splitlines()]
    return placed


def _identity(dataset: str, row: dict[str, object]) -> object:
    return row["step_key"] if dataset == "steps" else row["usage_id"]


def _without_export_time(row: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in row.items() if key != "exported_at"}


@pytest.mark.parametrize("point", _EGRESS_SWEEP)
def test_kill9_at_egress_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A kill between placing files and recording the cursor writes the same rows again, in new files."""
    hub_dir, runner_dir, directory = tmp_path / "hub", tmp_path / "runner", tmp_path / "egress"
    hub_port, runner_port = free_port(), free_port()
    _scaffold_egress_hub(hub_dir, directory)

    # On from the first start: the cursor anchors at enable time.
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        chunk_id, _ = _ingest_hub_item_chunk(hub, _no_op_graph_yaml())
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        runner_proc = start_runner(runner_dir, crash_point=None)

        code = wait_death(hub_proc, timeout=90.0)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

        killed = _egress_data_files(directory)
        assert killed, "the killed pass had placed no data file"
        manifests = sorted((directory / "_manifests").glob("*.json")) if (directory / "_manifests").exists() else []
        assert bool(manifests) == (point == "egress.after-commit.before-cursor"), (
            f"{point}: the manifest is present exactly when the pass was killed after committing it"
        )
        assert all(count == 0 for _, count, _ in _egress_cursor_rows(hub_dir)), (
            "a cursor row recorded rows the killed pass never got to record"
        )
        killed_bytes = {name: (directory / name).read_bytes() for name in killed}

        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)
        status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
        assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
        time.sleep(_EGRESS_SWEEP_SECONDS * 4)
        _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)

    placed = _egress_data_files(directory)
    assert {name: (directory / name).read_bytes() for name in killed} == killed_bytes, (
        "a file the killed pass placed was changed"
    )
    for name, rows in killed.items():
        dataset = name.split("/", 1)[0]
        for row in rows:
            later = [
                copy
                for other, copies in placed.items()
                if other not in killed and other.startswith(f"{dataset}/")
                for copy in copies
                if _identity(dataset, copy) == _identity(dataset, row)
            ]
            assert later, f"{dataset} row {_identity(dataset, row)} of the killed pass was never written again"
            assert _without_export_time(later[-1]) == _without_export_time(row)

    named: set[str] = set()
    for dataset, count, files in _egress_cursor_rows(hub_dir):
        if count == 0:
            assert files == []
            continue
        *data, manifest = files
        listed = json.loads((directory / manifest).read_text())["files"]
        assert [entry["path"] for entry in listed] == data
        assert sum(entry["rows"] for entry in listed) == count
        assert {entry["dataset"] for entry in listed} == {dataset}
        named.update(data)
    assert named
    assert not named & set(killed), "a cursor row names a file of the pass that was killed before recording it"


# --- The lease trace sweep — the runner tells closed leases to an in-test OTLP sink ---


def _trace_runner_config(runner_dir: Path) -> None:
    """Tune the scaffolded runner's ``[tracing]`` knobs to a fast, zero-settle cadence before it starts."""
    toml = runner_dir / "blizzard-host.toml"
    text = toml.read_text()
    for commented, live in (
        ("# sweep_seconds = 60", f"sweep_seconds = {_TRACE_SWEEP_SECONDS}"),
        ("# settle_seconds = 300", "settle_seconds = 0"),
    ):
        assert commented in text, f"scaffolded runner config no longer renders {commented!r}"
        text = text.replace(commented, live, 1)
    toml.write_text(text)


def _lease_cursor_rows(runner_dir: Path) -> list[tuple[int, int]]:
    """Every runner ``trace_cursor`` row as ``(id, span_count)``, oldest first."""
    with _audit_connection(runner_dir) as conn:
        rows = conn.execute(
            select(runner_schema.trace_cursor.c.id, runner_schema.trace_cursor.c.span_count).order_by(
                runner_schema.trace_cursor.c.id
            )
        ).all()
    return [(row.id, row.span_count) for row in rows]


@pytest.mark.parametrize("point", _LEASE_TRACE_SWEEP)
def test_kill9_at_lease_trace_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` of the runner after the exporter accepted a batch but before its cursor row
    lands re-sends the same span ids on the next pass, and one cursor row then covers them."""
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()

    with otlp_sink() as sink:
        # Tracing is on from the runner's first start, before any lease closes: the cursor
        # opens at enable time, so a lease closed earlier would never be told.
        otel_env = {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": sink.traces_endpoint}
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, work_sources=())
        runner_proc = None
        hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
        try:
            await_http(hub, "/api/health", proc=hub_proc)
            chunk_id, _ = _ingest_hub_item_chunk(hub, _no_op_graph_yaml())
            write_runner_config(
                runner_dir,
                workspace=crash_env.workspace,
                bin_dir=crash_env.bin_dir,
                hub_port=hub_port,
                port=runner_port,
            )
            _trace_runner_config(runner_dir)
            runner_proc = start_runner(runner_dir, crash_point=point, extra_env=otel_env)

            code = wait_death(runner_proc, timeout=90.0)
            assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"
            _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)

            killed_requests = len(sink.requests)
            assert killed_requests == 1, f"expected one accepted export before the kill, saw {killed_requests}"
            told_once = [span.span_id for span in sink.spans()]
            assert told_once, "the export accepted before the kill carried no spans"
            assert all(count == 0 for _, count in _lease_cursor_rows(runner_dir)), (
                "a cursor row recorded spans the killed pass never got to record"
            )

            runner_proc = start_runner(runner_dir, crash_point=None, extra_env=otel_env)
            status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"})
            assert status == "done", f"chunk did not converge to done after kill at {point} (last {status!r})"
            _wait_requests(sink, killed_requests + 1, timeout=30.0)
            # Let any lease that closed after the re-send be told too, so every cursor row is final.
            time.sleep(_TRACE_SWEEP_SECONDS * 3)
            _assert_invariants(runner_dir, hub_dir, when=f"after convergence past {point}", after_recovery=True)
        finally:
            hub.close()
            terminate(runner_proc)
            terminate(hub_proc)

        resent = [
            span.span_id
            for request in sink.requests[killed_requests : killed_requests + 1]
            for rs in request.resource_spans
            for ss in rs.scope_spans
            for span in ss.spans
        ]
        all_ids = [span.span_id for span in sink.spans()]
        twice = {span_id: all_ids.count(span_id) for span_id in told_once}
        assert all(count == 2 for count in twice.values()), f"span ids not told exactly twice: {twice}"
        assert set(told_once) <= set(resent), "the first pass after restart did not re-send the killed batch"

        advancing = [count for _, count in _lease_cursor_rows(runner_dir) if count > 0]
        assert advancing, "no cursor row recorded the re-sent batch"
        assert advancing[0] == len(resent), (
            f"the first advancing cursor row covers {advancing[0]} spans, not the re-sent {len(resent)}"
        )
        assert sum(advancing) == len(sink.spans()) - len(told_once), (
            "cursor rows do not cover each post-restart span exactly once"
        )


# --- The host's own fact lane — a registered host pushes its facts and records the hub's mark ---


def _host_lane(runner_dir: Path) -> tuple[list[int], int | None]:
    """The seqs waiting on the host's own lane, and the hub's answered mark the host recorded."""
    with _audit_connection(runner_dir) as conn:
        pending = conn.execute(
            select(runner_schema.host_outbound_buffer.c.seq).order_by(runner_schema.host_outbound_buffer.c.seq)
        ).scalars()
        buffered = list(pending)
        mark = conn.execute(select(runner_schema.host_high_water_seen.c.seq)).scalar_one_or_none()
    return buffered, mark


def _hub_host_mark(hub_dir: Path, host_id: str) -> int | None:
    """The per-host mark the hub holds for ``host_id``."""
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                hub_scoped(
                    select(hub_schema.host_high_water.c.seq).where(hub_schema.host_high_water.c.host_id == host_id)
                )
            ).scalar_one_or_none()
    finally:
        engine.dispose()


@pytest.mark.parametrize("point", _HOST_LANE_SWEEP)
def test_kill9_at_host_lane_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` of a registered host after its hub applied a lane push, before the host records the mark,
    leaves the facts buffered; the restarted host re-pushes them, the hub answers them already applied, and the
    host clears them and records the mark the hub holds."""
    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    hub_port, runner_port = free_port(), free_port()
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, work_sources=())
    runner_proc = None
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        write_runner_config(
            runner_dir, workspace=crash_env.workspace, bin_dir=crash_env.bin_dir, hub_port=hub_port, port=runner_port
        )
        # `runner init` registered the host and wrote its token, and the runner stands under it from its start.
        host_token = assigned_token((runner_dir / ".env").read_text(), "BZ_HOST_TOKEN")
        assert host_token, "runner init wrote no host token"
        record = hub.get("/api/fleet/host", headers={"Authorization": f"Bearer {host_token}"})
        assert record.status_code == 200, record.text
        host_id = record.json()["host_id"]
        runner_proc = start_runner(runner_dir, crash_point=point)

        code = wait_death(runner_proc, timeout=90.0)
        assert code == -9, f"armed runner at {point} exited {code}, not SIGKILL (-9); point never reached?"
        _assert_invariants(runner_dir, hub_dir, when=f"immediately after kill at {point}", after_recovery=False)
        buffered, mark = _host_lane(runner_dir)
        assert buffered, "the killed push left no fact buffered on the host's lane"
        assert mark is None, f"the host recorded a mark {mark} the killed pass never got to record"
        applied = _hub_host_mark(hub_dir, host_id)
        assert applied is not None and applied >= max(buffered), (applied, buffered)

        runner_proc = start_runner(runner_dir, crash_point=None)
        assert _poll(lambda: (mark := _host_lane(runner_dir)[1]) is not None and mark >= max(buffered), 60.0), (
            f"the restarted host never recorded the hub's mark past {buffered}"
        )
        assert not set(buffered) & set(_host_lane(runner_dir)[0]), "a re-pushed fact stayed on the host's lane"
        _assert_invariants(runner_dir, hub_dir, when=f"after recovery past {point}", after_recovery=True)
    finally:
        hub.close()
        terminate(runner_proc)
        terminate(hub_proc)


def _poll(done: Any, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(0.5)
    return False


# --- Tenant deletion — the hub drains a closed tenant in batches and the sweep finishes what a kill cut short ---

#: Work refs the doomed tenant holds beyond its packaged graphs and its chunk history: past one drain batch, so the
#: drain commits a batch and has a next one to begin, and that batch ends among the work refs — the restart and the
#: migration drained after them outlive the epoch owner and the route release drained before them.
_DOOMED_FILLER_ROWS = DRAIN_BATCH_ROWS + 500
_TEARDOWN_HISTORY_AT = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
_BYSTANDER_FILLER_ROWS = 50

#: The rows each point's kill leaves the deletion fact counting, given the rows the drain removes in all.
_REMOVED_AT_KILL = {
    "teardown.after-close.before-drain": lambda total: 0,
    "teardown.after-batch.before-next": lambda total: DRAIN_BATCH_ROWS,
    "teardown.after-drain.before-complete": lambda total: total,
}

#: The tables a deletion removes the tenant from: its own rows as it drains, then the tenant itself and its
#: memberships as it completes — the deletion counts every one.
_TEARDOWN_TABLES = [t for t in hub_schema.metadata.sorted_tables if is_tenant_owned(t)] + [
    hub_schema.tenants,
    hub_schema.membership_facts,
]


def _fill_keyed_locks(hub_dir: Path, tenant: str, count: int) -> None:
    """Give ``tenant`` ``count`` lock rows of its own — rows of a table no other row depends on."""
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                hub_scoped(insert(hub_schema.keyed_locks)),
                [{"tenant_id": tenant, "namespace": "crash-teardown", "key": f"k{n}"} for n in range(count)],
            )
    finally:
        engine.dispose()


def _seed_doomed_history(hub_dir: Path, tenant: str) -> None:
    """Give ``tenant`` a chunk history, pinned to one of its packaged graphs, that its drain takes apart table by table."""
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        with engine.begin() as conn:
            nodes = hub_schema.graph_nodes.c
            graph_id, node_id = conn.execute(
                hub_scoped(
                    select(nodes.graph_id, nodes.node_id)
                    .where(nodes.tenant_id == tenant, nodes.executor == Executor.RUNNER.value)
                    .order_by(nodes.node_id)
                    .limit(1)
                )
            ).one()
            seed_drainable_history(
                conn,
                TenantId(tenant),
                graph_id=graph_id,
                node_id=node_id,
                at=_TEARDOWN_HISTORY_AT,
                open_refs=_DOOMED_FILLER_ROWS,
            )
    finally:
        engine.dispose()


def _tenant_rows(hub_dir: Path, tenant: str) -> dict[str, set[tuple[object, ...]]]:
    """Every row naming ``tenant``, by table — only tables holding any."""
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        with engine.connect() as conn:
            rows = {
                t.name: {tuple(row) for row in conn.execute(hub_scoped(select(t).where(t.c.tenant_id == tenant)))}
                for t in _TEARDOWN_TABLES
            }
    finally:
        engine.dispose()
    return {name: held for name, held in rows.items() if held}


def _drained_rows(rows: dict[str, set[tuple[object, ...]]]) -> int:
    """The rows a drain removes — every one but the tenant's own row and its memberships, which completion removes."""
    return sum(len(held) for name, held in rows.items() if name not in {"tenants", "membership_facts"})


def _deletion_fact(hub_dir: Path, tenant: str) -> Row[tuple[object, ...]] | None:
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        with engine.connect() as conn:
            table = hub_schema.tenant_deletions
            return conn.execute(hub_scoped(select(table).where(table.c.tenant_id == tenant))).one_or_none()
    finally:
        engine.dispose()


def _wait_deleted(hub_dir: Path, tenant: str, *, timeout: float) -> Row[tuple[object, ...]] | None:
    """Poll ``tenant``'s deletion fact until its deletion completes; the last fact read."""
    deadline = time.monotonic() + timeout
    fact = _deletion_fact(hub_dir, tenant)
    while time.monotonic() < deadline and (fact is None or fact.deleted_at is None):
        time.sleep(0.5)
        fact = _deletion_fact(hub_dir, tenant)
    return fact


def _assert_hub_invariants(hub_dir: Path, *, when: str) -> None:
    violations = Invariants(hub_db_url=load_hub_config(hub_dir).db_url).run()
    assert not violations, f"invariant violations {when}:\n" + "\n".join(str(v) for v in violations)


@pytest.mark.parametrize("point", _TEARDOWN_SWEEP)
def test_kill9_at_teardown_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` inside a tenant's deletion — after its close, between two drain batches, or after its drain — is
    finished by the restarted hub's ``tenant_teardown`` sweep: nothing of the tenant is left, its deletion counts
    every row it removed exactly once, and the other tenant keeps every row it had."""
    hub_dir, hub_port = tmp_path / "hub", free_port()
    hub = httpx2.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, work_sources=())
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        created = hub.post("/api/admin/tenants", json={"name": "doomed"})
        assert created.status_code == 201, created.text
        doomed = created.json()["tenant_id"]
        listed = hub.get("/api/admin/tenants")
        assert listed.status_code == 200, listed.text
        [bystander] = [t["tenant_id"] for t in listed.json() if t["tenant_id"] != doomed]
        terminate(hub_proc)

        _seed_doomed_history(hub_dir, doomed)
        _fill_keyed_locks(hub_dir, bystander, _BYSTANDER_FILLER_ROWS)
        doomed_before, bystander_before = _tenant_rows(hub_dir, doomed), _tenant_rows(hub_dir, bystander)
        total = _drained_rows(doomed_before)
        assert total > DRAIN_BATCH_ROWS, "the doomed tenant fits one drain batch; no batch would have a next one"

        # Restart armed: the delete request's own inline drain reaches the point and the hub dies mid-request.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=point)
        await_http(hub, "/api/health", proc=hub_proc)
        with contextlib.suppress(httpx2.HTTPError):
            hub.delete(f"/api/admin/tenants/{doomed}")
        code = wait_death(hub_proc, timeout=60.0)
        assert code == -9, f"armed hub at {point} exited {code}, not SIGKILL (-9); point never reached?"

        killed = _deletion_fact(hub_dir, doomed)
        assert killed is not None and killed.deleted_at is None, f"{point}: the deletion is not underway: {killed}"
        assert killed.rows_removed == _REMOVED_AT_KILL[point](total), (
            f"{point}: the deletion counts {killed.rows_removed} rows removed"
        )
        assert _drained_rows(_tenant_rows(hub_dir, doomed)) == total - killed.rows_removed, (
            f"{point}: the rows left and the rows counted removed disagree — a batch and its count split"
        )
        if point == "teardown.after-batch.before-next":
            left = _tenant_rows(hub_dir, doomed)
            assert "epoch_owners" not in left and {"chunk_restarts", "chunk_migrations"} <= left.keys(), (
                f"the kill's batch boundary leaves no half-drained history: {sorted(left)}"
            )
        _assert_hub_invariants(hub_dir, when=f"immediately after kill at {point}")

        # Restart unarmed: the sweep's first pass finds the deletion underway and finishes it.
        hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None)
        await_http(hub, "/api/health", proc=hub_proc)
        deleted = _wait_deleted(hub_dir, doomed, timeout=60.0)
        assert deleted is not None and deleted.deleted_at is not None, f"deletion not completed after kill at {point}"
        held = sum(len(rows) for rows in doomed_before.values())
        assert deleted.rows_removed == held, (
            f"{point}: the deletion counts {deleted.rows_removed} rows removed, not the {held} the tenant held"
        )
        assert _tenant_rows(hub_dir, doomed) == {}, f"rows of the deleted tenant survive the kill at {point}"
        bystander_after = _tenant_rows(hub_dir, bystander)
        lost = {name: held - bystander_after.get(name, set()) for name, held in bystander_before.items()}
        assert not any(lost.values()), f"the other tenant lost rows across the kill at {point}: {lost}"
        assert hub.delete(f"/api/admin/tenants/{doomed}").status_code == 410
        assert [t["tenant_id"] for t in hub.get("/api/admin/tenants").json()] == [bystander]
        _assert_hub_invariants(hub_dir, when=f"after the deletion completed past {point}")
    finally:
        hub.close()
        terminate(hub_proc)


# --- Ingest — `blizzard runner ingest` moves a runner between two hosts registered at one hub ---

_INGEST_MOVING = "r2"
_INGEST_WORKSPACE = "ws-there"


def _runner_cli(hub_url: str, *args: str, crash_point: str | None = None) -> subprocess.CompletedProcess[str]:
    env = runner_spawn_env(hub_url)
    if crash_point is not None:
        env[ENV_CRASH_FENCE] = "1"
        env[ENV_CRASH_POINT] = crash_point
    return subprocess.run([str(RUNNER_BIN), *args], capture_output=True, text=True, env=env, check=False, timeout=120)


def _cli_ok(hub_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    done = _runner_cli(hub_url, *args)
    assert done.returncode == 0, f"{args} failed ({done.returncode}):\n{done.stdout}\n{done.stderr}"
    return done


def _host_id(hub: httpx2.Client, root: Path) -> str:
    token = assigned_token((root / ".env").read_text(), HOST_TOKEN_VARIABLE)
    assert token, f"{root} holds no host token"
    record = hub.get("/api/fleet/host", headers={"Authorization": f"Bearer {token}"})
    assert record.status_code == 200, record.text
    return str(record.json()["host_id"])


def _ingest_world(hub: httpx2.Client, hub_url: str, here: Path, there: Path) -> str:
    """``here`` and ``there``, each a host registered at the hub, ``there`` holding ``r2`` beside its own runner with
    one row in every runner table, and the hub holding ``r2`` under ``here`` — as `blizzard hub runner-host release`
    then `adopt` leave it. Answers ``r2``'s id."""
    _cli_ok(hub_url, "init", str(here), "--hub", hub_url)
    # A host is named after its first runner, and two live hosts cannot share a name.
    layout = HostLayout(init_host(there, hub_url=hub_url).root)
    layout.config_path.write_text(layout.config_path.read_text().replace('"runner-local"', '"runner-there"', 1))
    _cli_ok(hub_url, "init", str(there), "--hub", hub_url)
    _cli_ok(hub_url, "add", _INGEST_MOVING, "--project", "default", "--dir", str(there))
    # Its identity is recorded in the store as `init` keeps a runner the hub holds.
    _cli_ok(hub_url, "init", str(there), "--hub", hub_url)
    moving_id = load_host_config(there).sole_runner(_INGEST_MOVING).id
    assert moving_id is not None
    for verb, root in (("releases", there), ("adoptions", here)):
        moved = hub.post(
            f"/api/hosts/{_host_id(hub, root)}/{verb}", json={"by": "crash-sweep", "runner_ids": [moving_id]}
        )
        assert moved.status_code == 200, moved.text
    engine = create_engine_from_url(load_host_config(there).db_url)
    try:
        seed_runner(engine, moving_id, tag="b", workspace_id="workspace-local")
    finally:
        engine.dispose()
    return moving_id


def _ingest_end_state(here: Path, there: Path, moving_id: str) -> dict[str, object]:
    """What an ingest leaves: the runner's rows here and in the source, both host files' runners and workspaces, and
    where its token is."""
    variable = hub_token_variable(_INGEST_MOVING)
    here_engine = create_engine_from_url(load_host_config(here).db_url)
    there_engine = create_engine_from_url(load_host_config(there).db_url)
    try:
        state: dict[str, object] = {
            "here": {name: contents(here_engine, name, runner_id=moving_id) for name in RUNNER_TABLES},
            "there": {name: contents(there_engine, name, runner_id=moving_id) for name in RUNNER_TABLES},
            "here violations": [str(v) for v in invariant_violations(here_engine)],
            "there violations": [str(v) for v in invariant_violations(there_engine)],
        }
    finally:
        here_engine.dispose()
        there_engine.dispose()
    for name, root in (("here", here), ("there", there)):
        config = load_host_config(root)
        state[f"{name} runners"] = sorted((r.name, r.id) for r in config.runners)
        state[f"{name} workspaces"] = sorted(w.id for w in config.workspaces)
        state[f"{name} token"] = bool(HubTokenFile.of(root, variable).held())
    return state


def _expected_ingest(moving: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """``moving`` as an ingest under ``--workspace-id`` copies it: each binding in the new workspace."""
    return {
        name: sorted(({**row, "workspace_id": _INGEST_WORKSPACE} for row in held), key=repr)
        if name == "env_bindings"
        else held
        for name, held in moving.items()
    }


@pytest.mark.parametrize("point", _INGEST_SWEEP)
def test_kill9_at_ingest_crash_point(crash_env: CrashEnv, tmp_path: Path, point: str) -> None:
    """A ``kill -9`` of ``blizzard runner ingest`` after it copied the runner's rows, or after it moved the runner's
    configuration and token, is completed by an unarmed re-run: the end state is the one an uninterrupted ingest
    leaves, and both stores hold their invariants at every step."""
    hub_dir, hub_port = tmp_path / "hub", free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    here, there = tmp_path / "here", tmp_path / "there"
    hub_proc = start_hub(hub_dir, forge_port=crash_env.forge_port, port=hub_port, crash_point=None, work_sources=())
    hub = httpx2.Client(base_url=hub_url, timeout=30.0)
    try:
        await_http(hub, "/api/health", proc=hub_proc)
        moving_id = _ingest_world(hub, hub_url, here, there)
        engine = create_engine_from_url(load_host_config(there).db_url)
        try:
            moving = {name: contents(engine, name, runner_id=moving_id) for name in RUNNER_TABLES}
        finally:
            engine.dispose()
        before = _ingest_end_state(here, there, moving_id)
        ingest = ("ingest", str(there), "--runner", _INGEST_MOVING, "--workspace-id", _INGEST_WORKSPACE)

        killed = _runner_cli(hub_url, *ingest, "--dir", str(here), crash_point=point)
        assert killed.returncode == -9, f"armed ingest at {point} exited {killed.returncode}, not SIGKILL (-9):\n" + (
            killed.stdout + killed.stderr
        )
        at_kill = _ingest_end_state(here, there, moving_id)
        assert at_kill["here"] == _expected_ingest(moving), f"{point}: the copy is not whole at the kill"
        assert at_kill["there"] == moving, f"{point}: the source lost rows before its removal"
        assert (at_kill["here violations"], at_kill["there violations"]) == ([], []), at_kill
        assert at_kill["there token"] is (point == "ingest.after-copy.before-config"), at_kill

        resumed = _runner_cli(hub_url, *ingest, "--dir", str(here))
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        assert "completed the ingest of runner r2" in resumed.stdout, resumed.stdout
        for root in (here, there):
            violations = Invariants(
                runner_db_url=load_host_config(root).db_url, hub_db_url=load_hub_config(hub_dir).db_url
            ).run(after_recovery=False)
            assert not violations, f"invariant violations in {root.name} after the re-run past {point}:\n" + "\n".join(
                str(v) for v in violations
            )
    finally:
        hub.close()
        terminate(hub_proc)

    end = _ingest_end_state(here, there, moving_id)
    assert end == {
        "here": _expected_ingest(moving),
        "there": {name: [] for name in RUNNER_TABLES},
        "here violations": [],
        "there violations": [],
        "here runners": sorted([*before["here runners"], (_INGEST_MOVING, moving_id)]),  # type: ignore[misc]
        "there runners": before["there runners"],
        "here workspaces": ["workspace-local", _INGEST_WORKSPACE],
        "there workspaces": ["workspace-local"],
        "here token": True,
        "there token": False,
    }, f"{point}: the re-run's end state differs from an uninterrupted ingest's"
