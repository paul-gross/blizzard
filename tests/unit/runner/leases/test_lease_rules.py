"""The lease's rules, pinned by value: the worker-token check, the staleness baseline, the
activity assembly, and the worker verbs each lease standing accepts."""

from __future__ import annotations

from collections.abc import Container, Sequence
from datetime import UTC, datetime, timedelta
from typing import TypedDict, Unpack

import pytest

from blizzard.foundation.tokens import TokenHash
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.leases.activity import (
    HEARTBEAT_STALENESS_THRESHOLD,
    NO_PROGRESS_THRESHOLD,
    LeaseActivity,
    Liveness,
    progress_anchor,
    warns_no_progress,
)
from blizzard.runner.leases.execution_observation import WarnedStall
from blizzard.runner.leases.lease_auth import LeaseToken, LeaseTokenRejected
from blizzard.runner.leases.liveness import ExecutionEvidence, LeaseLivenessFacts
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.worker_lease import WORKER_VERBS, WorkerLease, WorkerLeaseStanding, WorkerVerb

_MINT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _lease(*, pid: int | None = 7, session_id: str | None = "sess") -> Lease:
    return Lease(
        lease_id="lease_1",
        chunk_id="ch_1",
        graph_id="gr_1",
        node_id="nd_build",
        node_name="build",
        epoch=1,
        retries_max=2,
        created_at=_MINT,
        pid=pid,
        session_id=session_id,
        harness_id="claude-code" if session_id is not None else None,
    )


def _binding(env: str, *, chunk: str = "ch_1") -> EnvBinding:
    return EnvBinding(chunk_id=chunk, environment_id=env, workdir=f"/ws/{env}", bound_at=_MINT)


# --- LeaseToken.require ---------------------------------------------------------------


def test_a_matching_token_passes() -> None:
    LeaseToken("tok", TokenHash("tok").hex).require("lease_1")


@pytest.mark.parametrize(("presented", "stored"), [("wrong", TokenHash("tok").hex), (None, TokenHash("tok").hex)])
def test_a_wrong_or_missing_token_is_rejected_with_the_lease_named(presented: str | None, stored: str) -> None:
    with pytest.raises(LeaseTokenRejected) as exc:
        LeaseToken(presented, stored).require("lease_1")
    assert str(exc.value) == "presented token does not authorize lease lease_1"


def test_a_lease_that_never_minted_a_token_rejects_any_token() -> None:
    with pytest.raises(LeaseTokenRejected):
        LeaseToken("tok", None).require("lease_1")


# --- Liveness.of ----------------------------------------------------------------------


def test_the_baseline_is_the_mint_with_neither_fact() -> None:
    assert Liveness.of(_lease(), heartbeat=None, spawn=None).last_activity == _MINT


def test_the_baseline_is_the_newest_of_mint_heartbeat_and_spawn() -> None:
    beat, spawn = _MINT + timedelta(minutes=5), _MINT + timedelta(minutes=9)
    assert Liveness.of(_lease(), heartbeat=beat, spawn=spawn).last_activity == spawn
    assert Liveness.of(_lease(), heartbeat=spawn, spawn=beat).last_activity == spawn
    assert Liveness.of(_lease(), heartbeat=beat, spawn=None).last_activity == beat


def test_a_fact_older_than_the_mint_never_lowers_the_baseline() -> None:
    assert Liveness.of(_lease(), heartbeat=_MINT - timedelta(hours=1), spawn=None).last_activity == _MINT


def test_current_evidence_joins_the_baseline() -> None:
    spawn, evidence = _MINT + timedelta(minutes=1), _MINT + timedelta(hours=2)
    facts = LeaseLivenessFacts(
        latest_heartbeat=None,
        latest_spawn=spawn,
        generation=2,
        latest_evidence=ExecutionEvidence(generation=2, evidence_at=evidence),
    )
    assert Liveness.of_facts(_lease(), facts).last_activity == evidence


@pytest.mark.parametrize(
    ("generation", "evidence_at"),
    [
        (1, _MINT + timedelta(hours=2)),  # an older generation's evidence
        (2, _MINT),  # evidence produced before the current spawn
    ],
)
def test_evidence_not_of_the_current_execution_never_counts(generation: int, evidence_at: datetime) -> None:
    spawn = _MINT + timedelta(minutes=1)
    facts = LeaseLivenessFacts(
        latest_heartbeat=None,
        latest_spawn=spawn,
        generation=2,
        latest_evidence=ExecutionEvidence(generation=generation, evidence_at=evidence_at),
    )
    assert facts.current_evidence() is None
    assert Liveness.of_facts(_lease(), facts).last_activity == spawn


# --- the no-progress warning -----------------------------------------------------------


def test_the_progress_anchor_is_the_newest_of_spawn_heartbeat_and_reported_progress() -> None:
    spawn = _MINT
    assert progress_anchor(spawn=spawn, heartbeat=None, progress=None) == spawn
    later = _MINT + timedelta(minutes=3)
    assert progress_anchor(spawn=spawn, heartbeat=later, progress=_MINT + timedelta(minutes=1)) == later
    assert progress_anchor(spawn=spawn, heartbeat=_MINT + timedelta(minutes=1), progress=later) == later


def test_a_stall_past_the_threshold_warns_only_while_alive() -> None:
    now = _MINT + NO_PROGRESS_THRESHOLD + timedelta(seconds=1)
    assert warns_no_progress(alive=True, generation=1, anchor=_MINT, warned=None, now=now)
    assert not warns_no_progress(alive=False, generation=1, anchor=_MINT, warned=None, now=now)
    at_line = _MINT + NO_PROGRESS_THRESHOLD
    assert not warns_no_progress(alive=True, generation=1, anchor=_MINT, warned=None, now=at_line)


def test_a_stall_warns_once_and_a_moved_anchor_or_new_generation_re_arms() -> None:
    now = _MINT + timedelta(hours=1)
    warned = WarnedStall(generation=1, anchor_at=_MINT)
    assert not warns_no_progress(alive=True, generation=1, anchor=_MINT, warned=warned, now=now)
    moved = _MINT + timedelta(minutes=10)
    assert warns_no_progress(alive=True, generation=1, anchor=moved, warned=warned, now=now)
    assert warns_no_progress(alive=True, generation=2, anchor=_MINT, warned=warned, now=now)


# --- LeaseActivity.of -----------------------------------------------------------------


class _ActivityFields(TypedDict, total=False):
    facts: LeaseLivenessFacts | None
    parked_ids: Container[str]
    backing_off: Container[str]
    bindings: Sequence[EnvBinding]
    alive: bool
    now: datetime
    stale_after: timedelta


def _activity(*, lease: Lease | None = None, **overrides: Unpack[_ActivityFields]) -> LeaseActivity:
    fields: _ActivityFields = {
        "facts": LeaseLivenessFacts(latest_heartbeat=_MINT, latest_spawn=_MINT),
        "parked_ids": set(),
        "backing_off": set(),
        "bindings": [],
        "alive": True,
        "now": _MINT + timedelta(minutes=1),
        "stale_after": HEARTBEAT_STALENESS_THRESHOLD,
    }
    fields.update(overrides)
    return LeaseActivity.of(lease or _lease(), **fields)


def test_a_fresh_live_spawned_lease_is_running() -> None:
    assert _activity().state == "running"


def test_parked_outranks_everything() -> None:
    assert _activity(parked_ids={"lease_1"}, backing_off={"lease_1"}, alive=False).state == "parked"


def test_backing_off_outranks_spawning_and_exited() -> None:
    assert _activity(backing_off={"lease_1"}, alive=False).state == "backing-off"


def test_no_pid_or_no_session_reads_spawning() -> None:
    assert _activity(lease=_lease(pid=None)).state == "spawning"
    assert _activity(lease=_lease(session_id=None)).state == "spawning"


def test_a_dead_process_reads_exited() -> None:
    assert _activity(alive=False).state == "exited"


def test_a_beat_older_than_the_threshold_reads_stale() -> None:
    now = _MINT + HEARTBEAT_STALENESS_THRESHOLD + timedelta(seconds=1)
    assert _activity(now=now).state == "stale"
    assert _activity(now=now, stale_after=HEARTBEAT_STALENESS_THRESHOLD * 2).state == "running"


def test_no_liveness_facts_measures_from_the_mint_and_reports_no_heartbeat() -> None:
    activity = _activity(facts=None)
    assert activity.last_heartbeat_at is None
    assert activity.stale is False


def test_the_reported_heartbeat_is_the_heartbeat_fact_not_the_spawn() -> None:
    beat, spawn = _MINT + timedelta(seconds=10), _MINT + timedelta(seconds=20)
    activity = _activity(facts=LeaseLivenessFacts(latest_heartbeat=beat, latest_spawn=spawn))
    assert activity.last_heartbeat_at == beat


def test_each_liveness_fact_alone_reaches_the_staleness_baseline() -> None:
    fresh = _MINT + HEARTBEAT_STALENESS_THRESHOLD
    now = fresh + timedelta(seconds=1)
    by_beat = _activity(facts=LeaseLivenessFacts(latest_heartbeat=fresh, latest_spawn=None), now=now)
    by_spawn = _activity(facts=LeaseLivenessFacts(latest_heartbeat=None, latest_spawn=fresh), now=now)
    assert (by_beat.stale, by_spawn.stale) == (False, False)
    assert by_spawn.last_heartbeat_at is None


def test_the_first_binding_is_the_one_shown() -> None:
    activity = _activity(bindings=[_binding("e2"), _binding("e3")])
    assert (activity.environment_id, activity.workdir) == ("e2", "/ws/e2")
    assert (_activity().environment_id, _activity().workdir) == (None, None)


# --- worker verbs by standing ---------------------------------------------------------


def test_the_active_lease_accepts_every_worker_verb() -> None:
    worker = WorkerLease(lease=_lease(), active=True)
    assert worker.standing is WorkerLeaseStanding.ACTIVE
    assert all(worker.accepts(verb) for verb in WorkerVerb)


def test_a_takeover_reference_lease_accepts_only_a_git_commit() -> None:
    worker = WorkerLease(lease=_lease(), active=False)
    assert worker.standing is WorkerLeaseStanding.TAKEOVER_REFERENCE
    assert {verb for verb in WorkerVerb if worker.accepts(verb)} == {WorkerVerb.GIT_COMMIT}
    assert set(WORKER_VERBS) == set(WorkerLeaseStanding)
