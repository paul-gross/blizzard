"""The lease's rules, pinned by value: the worker-token check, the staleness baseline, the
activity assembly, and the worker verbs each lease standing accepts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.tokens import TokenHash
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.leases import (
    HEARTBEAT_STALENESS_THRESHOLD,
    WORKER_VERBS,
    Lease,
    LeaseActivity,
    LeaseLivenessFacts,
    Liveness,
    WorkerLease,
    WorkerLeaseStanding,
    WorkerVerb,
)
from blizzard.runner.leases.lease_auth import LeaseToken, LeaseTokenRejected

pytestmark = pytest.mark.unit

_MINT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _lease(*, pid: int | None = 7, session_id: str | None = "sess") -> Lease:
    return Lease(
        lease_id="lease_1",
        chunk_id="ch_1",
        graph_id="gr_1",
        node_id="nd_build",
        node_name="build",
        epoch=1,
        runner_id="r1",
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


# --- LeaseActivity.of -----------------------------------------------------------------


def _activity(**overrides: object) -> LeaseActivity:
    fields: dict[str, object] = {
        "facts": LeaseLivenessFacts(latest_heartbeat=_MINT, latest_spawn=_MINT),
        "parked_ids": set(),
        "backing_off": set(),
        "bindings": [],
        "alive": True,
        "now": _MINT + timedelta(minutes=1),
        "stale_after": HEARTBEAT_STALENESS_THRESHOLD,
    }
    fields.update(overrides)
    lease = fields.pop("lease", _lease())
    return LeaseActivity.of(lease, **fields)  # type: ignore[arg-type]


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
