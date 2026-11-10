"""Every launch entry point a tick reaches escalates its own lease in place when a changed harness version refuses
the launch and its one relaunch: the lease closes unspent under its own reason and cause, and the same pass still
advances a second lease. The spawn sites (a fresh node entry, a requeue), the restart resume, the dormant wakes (on
unpause, on answer, the unmet-produces nudge) and the judge (first launch, lost-answer relaunch) are driven through the
real launch; the overload-backoff wake shares the same per-lease catch, driven by a refusal raised in its place."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import ESCALATION_RECORDED, EVENT_RECORDED, RUNNER_LOCALLY_PAUSED
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.foundation.node_steps import SessionMode
from blizzard.runner.harness.adapter import HarnessVersionChangedError, WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.hub.client import HubQuestion
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.attempt import Attempt
from blizzard.runner.lifecycle.dormant import DormantSession
from blizzard.runner.lifecycle.model import AdvanceMove
from blizzard.runner.lifecycle.resume_intents import ResumeIntents
from blizzard.runner.lifecycle.spawn import LaunchVersionRefusedError
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import Advance, Resume
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.store.schema import lease_closures
from tests.support.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_store,
    make_stores,
)
from tests.support.runner_mint import new_mint

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_REFUSAL = "the OpenCode service reported version 3.0.0, outside >=2,<3; restart the runner to take it up"
_REFUSED = "ch_refused"
_SIBLING = "ch_sibling"


class _HeldVersionHarness(FakeHarness):
    """Refuses every launch for ``refused`` chunks, as an adapter holding an out-of-range version does."""

    def __init__(self, *args: Any, refused: set[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.refused = refused
        self.refusals = 0

    def _refuse(self, chunk_id: str) -> None:
        if chunk_id in self.refused:
            self.refusals += 1
            raise HarnessVersionChangedError(_REFUSAL)

    def spawn(self, envelope: Any, *args: Any, **kwargs: Any) -> Any:
        self._refuse(envelope.chunk_id)
        return super().spawn(envelope, *args, **kwargs)

    def resume_with_message(self, *args: Any, **kwargs: Any) -> Any:
        self._refuse(kwargs.get("chunk_id", ""))
        return super().resume_with_message(*args, **kwargs)

    def judge(self, *args: Any, **kwargs: Any) -> Any:
        self._refuse(kwargs.get("chunk_id", ""))
        return super().judge(*args, **kwargs)


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _envelope(chunk_id: str):
    return make_envelope(chunk_id, "build", node_id="nd_build", choices=_CHOICES)


def _seed_spawned(store: SqlAlchemyRunnerStore, chunk_id: str, *, env: str) -> str:
    lease_id = f"lease_{chunk_id}"
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=lease_id,
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=_NOW,
            )
        )
    )
    store.record_spawn(
        lease_id,
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{chunk_id}"),
        spawned_at=_NOW,
        pgid=100,
    )
    store.record_binding(chunk_id=chunk_id, environment_id=env, workdir=f"/ws/{env}", bound_at=_NOW, workspace_id="ws1")
    return lease_id


def _rig(
    tmp_path: Path, *, judge_output: str = "pass"
) -> tuple[SqlAlchemyRunnerStore, FakeHub, _HeldVersionHarness, LoopContext]:
    store = _store(tmp_path)
    hub = FakeHub()
    harness = _HeldVersionHarness(
        handle=WorkerHandle(session_id="sess-new", pid=200, process_start_time="start-200", pgid=200),
        verdict="pass",
        judge_output=judge_output,
        refused={_REFUSED},
    )
    harness.resume_pid = 300
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=harness,
        probe=FakeProbe(alive=set()),
        clock=FixedClock(_NOW),
    )
    return store, hub, harness, ctx


def _closures(store: SqlAlchemyRunnerStore, chunk_id: str) -> list[tuple[str, str | None]]:
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(lease_closures.c.reason, lease_closures.c.escalation_cause)
            .where(lease_closures.c.runner_id == FIXTURE_RUNNER_ID, lease_closures.c.chunk_id == chunk_id)
            .order_by(lease_closures.c.id)
        ).all()
    return [(row.reason, row.escalation_cause) for row in rows]


def _assert_escalated_unspent(store: SqlAlchemyRunnerStore, *, spent_before: int, via: str) -> None:
    assert store.active_lease_for_chunk(_REFUSED) is None
    assert _closures(store, _REFUSED)[-1] == ("harness-version-refused", "harness-version-changed")
    assert store.attempt_count(_REFUSED, "nd_build") == spent_before
    escalation = store.open_escalation_for_chunk(_REFUSED)
    assert escalation is not None and escalation.cause == "harness-version-changed"
    recorded = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED and b.chunk_id == _REFUSED]
    assert len(recorded) == 1
    detail = json.loads(recorded[0].payload)["detail"]
    assert "3.0.0" in detail and "restart the runner on a supported release, then requeue" in detail
    events = [
        json.loads(b.payload) for b in store.pending_outbound() if b.kind == EVENT_RECORDED and b.chunk_id == _REFUSED
    ]
    changed = [e for e in events if e["kind"] == "harness-version-changed"]
    assert len(changed) == 1
    assert changed[0]["severity"] == "critical" and changed[0]["detail"]["via"] == via
    assert store.resume_intent_lease_ids() == set()


# --- spawn ------------------------------------------------------------------------------------------------------------


def test_a_fresh_node_entry_refused_escalates_its_mint_and_a_sibling_still_spawns(tmp_path: Path) -> None:
    store, hub, harness, ctx = _rig(tmp_path)
    for chunk_id, env in ((_REFUSED, "e1"), (_SIBLING, "e2")):
        prior = f"lease_{chunk_id}_prior"
        store.record_mint(
            new_mint(
                NewLease(
                    lease_id=prior,
                    chunk_id=chunk_id,
                    graph_id="gr_1",
                    node_id="nd_build",
                    node_name="build",
                    epoch=1,
                    retries_max=2,
                    created_at=_NOW,
                )
            )
        )
        store.record_closure(
            lease_id=prior, chunk_id=chunk_id, node_id="nd_build", reason="transitioned", closed_at=_NOW
        )
        store.record_binding(
            chunk_id=chunk_id, environment_id=env, workdir=f"/ws/{env}", bound_at=_NOW, workspace_id="ws1"
        )
        hub.chunks[chunk_id] = ChunkState(
            chunk_id=chunk_id, status=ChunkStatus.RUNNING, route_runner_id="r1", latest_epoch=2
        )
        hub.envelopes[chunk_id] = make_envelope(
            chunk_id, "verify", node_id="nd_verify", choices=_CHOICES, session=SessionMode.FRESH
        )

    Advance(ctx).run()  # must not raise

    assert harness.refusals == 2  # the launch and its one relaunch
    assert store.active_lease_for_chunk(_REFUSED) is None
    assert _closures(store, _REFUSED)[-1] == ("harness-version-refused", "harness-version-changed")
    assert store.attempt_count(_REFUSED, "nd_verify") == 0
    escalation = store.open_escalation_for_chunk(_REFUSED)
    assert escalation is not None and escalation.cause == "harness-version-changed"
    sibling = store.active_lease_for_chunk(_SIBLING)
    assert sibling is not None and sibling.session_id == "sess-new"


def test_a_requeue_refused_escalates_without_spending_another_retry(tmp_path: Path) -> None:
    store, hub, harness, ctx = _rig(tmp_path)
    lease_id = _seed_spawned(store, _REFUSED, env="e1")
    hub.envelopes[_REFUSED] = _envelope(_REFUSED)
    lease = store.active_lease(lease_id)
    assert lease is not None

    Attempt(ctx, lease).fail(reason=LeaseClosureReason.REAPED, via="test")  # must not raise

    assert harness.refusals == 2
    # The reaped attempt is the one spent; the refused requeue spends nothing more.
    _assert_escalated_unspent(store, spent_before=1, via="requeue")


# --- restart resume ---------------------------------------------------------------------------------------------------


def test_a_restart_resume_refused_escalates_that_lease_and_the_other_intent_still_resumes(tmp_path: Path) -> None:
    store, hub, _harness, ctx = _rig(tmp_path)
    _seed_spawned(store, _REFUSED, env="e1")
    sibling = _seed_spawned(store, _SIBLING, env="e2")
    for chunk_id in (_REFUSED, _SIBLING):
        hub.chunks[chunk_id] = ChunkState(
            chunk_id=chunk_id, status=ChunkStatus.RUNNING, route_runner_id="r1", latest_epoch=1
        )
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    Resume(ctx).run()  # must not raise

    _assert_escalated_unspent(store, spent_before=0, via="restart-resume")
    resumed = store.active_lease(sibling)
    assert resumed is not None and resumed.pid == 300


# --- dormant wakes ----------------------------------------------------------------------------------------------------


def test_an_unpause_wake_refused_escalates_and_a_sibling_wakes_in_the_same_pass(tmp_path: Path) -> None:
    store, hub, _harness, ctx = _rig(tmp_path)
    for chunk_id, env in ((_REFUSED, "e1"), (_SIBLING, "e2")):
        lease_id = _seed_spawned(store, chunk_id, env=env)
        store.record_pause_park(lease_id=lease_id, chunk_id=chunk_id, parked_at=_NOW)
        hub.chunks[chunk_id] = ChunkState(
            chunk_id=chunk_id, status=ChunkStatus.RUNNING, route_runner_id="r1", latest_epoch=1
        )

    Advance(ctx).run()  # must not raise

    _assert_escalated_unspent(store, spent_before=0, via=f"advance-{AdvanceMove.ON_UNPAUSE.value}")
    woken = store.active_lease_for_chunk(_SIBLING)
    assert woken is not None and woken.pid == 300


def _answered(chunk_id: str) -> HubQuestion:
    return HubQuestion(
        question_id=f"qn_{chunk_id}",
        chunk_id=chunk_id,
        runner_id="r1",
        epoch=1,
        question="Q",
        asked_at="t",
        answered=True,
        answer="rest",
        answered_by="alice",
        answered_at="t2",
    )


def test_an_answer_wake_refused_escalates_and_a_sibling_wakes_in_the_same_pass(tmp_path: Path) -> None:
    store, hub, _harness, ctx = _rig(tmp_path)
    for chunk_id, env in ((_REFUSED, "e1"), (_SIBLING, "e2")):
        lease_id = _seed_spawned(store, chunk_id, env=env)
        store.record_park(lease_id=lease_id, chunk_id=chunk_id, question_id=f"qn_{chunk_id}", parked_at=_NOW)
        hub.questions[f"qn_{chunk_id}"] = _answered(chunk_id)

    Advance(ctx).run()  # must not raise

    _assert_escalated_unspent(store, spent_before=0, via=f"advance-{AdvanceMove.ON_ANSWER.value}")
    woken = store.active_lease_for_chunk(_SIBLING)
    assert woken is not None and woken.pid == 300


# --- judge ------------------------------------------------------------------------------------------------------------


def test_a_judge_launch_refused_escalates_and_a_sibling_is_judged_in_the_same_pass(tmp_path: Path) -> None:
    store, hub, harness, ctx = _rig(tmp_path)
    for chunk_id, env in ((_REFUSED, "e1"), (_SIBLING, "e2")):
        _seed_spawned(store, chunk_id, env=env)
        hub.envelopes[chunk_id] = _envelope(chunk_id)

    Advance(ctx).run()  # must not raise

    _assert_escalated_unspent(store, spent_before=0, via=f"advance-{AdvanceMove.EXITED.value}")
    assert store.in_flight_elicitation(f"lease_{_REFUSED}", 1) is None
    assert [judged[1] for judged in harness.judged] == [f"sess-{_SIBLING}"]
    assert store.in_flight_elicitation(f"lease_{_SIBLING}", 1) is not None


def test_a_lost_answers_relaunch_refused_escalates(tmp_path: Path) -> None:
    store, hub, harness, ctx = _rig(tmp_path, judge_output="")  # writes nothing readable
    lease_id = _seed_spawned(store, _REFUSED, env="e1")
    hub.envelopes[_REFUSED] = _envelope(_REFUSED)
    harness.refused = set()
    Advance(ctx).run()  # the first launch runs
    assert store.in_flight_elicitation(lease_id, 1) is not None
    harness.refused = {_REFUSED}

    Advance(ctx).run()  # collect finds the answer lost and relaunches — refused, twice

    assert harness.refusals == 2
    _assert_escalated_unspent(store, spent_before=0, via=f"advance-{AdvanceMove.EXITED.value}")
    assert store.in_flight_elicitation(lease_id, 1) is None


# --- the shared per-lease catch ---------------------------------------------------------------------------------------


def _refuse(*_: object, **__: object) -> None:
    raise LaunchVersionRefusedError(CLAUDE_CODE_HARNESS_ID, _REFUSAL)


def test_an_overload_backoff_wake_refused_escalates_through_the_per_lease_catch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _hub, _harness, ctx = _rig(tmp_path)
    lease_id = _seed_spawned(store, _REFUSED, env="e1")

    def backing_off(*_: object) -> dict[str, object]:
        return {lease_id: object()}

    monkeypatch.setattr("blizzard.runner.lifecycle.advance.backing_off_facts", backing_off)
    monkeypatch.setattr(DormantSession, "on_overload_backoff", _refuse)

    Advance(ctx).run()  # must not raise

    _assert_escalated_unspent(store, spent_before=0, via=f"advance-{AdvanceMove.ON_OVERLOAD_BACKOFF.value}")


def test_an_unmet_produces_nudge_refused_escalates_and_a_sibling_is_nudged_in_the_same_pass(tmp_path: Path) -> None:
    store, hub, harness, ctx = _rig(tmp_path)
    for chunk_id, env in ((_REFUSED, "e1"), (_SIBLING, "e2")):
        _seed_spawned(store, chunk_id, env=env)
        hub.envelopes[chunk_id] = make_envelope(
            chunk_id, "build", node_id="nd_build", choices=_CHOICES, produces=["review-findings"]
        )

    Advance(ctx).run()  # must not raise

    _assert_escalated_unspent(store, spent_before=0, via=f"advance-{AdvanceMove.EXITED.value}")
    assert harness.judged == []
    assert [resumed[1] for resumed in harness.resumed] == [f"sess-{_SIBLING}"]


# --- precedence -------------------------------------------------------------------------------------------------------


def test_a_refusal_on_a_detached_chunk_abandons_instead(tmp_path: Path) -> None:
    store, hub, _harness, ctx = _rig(tmp_path)
    lease_id = _seed_spawned(store, _REFUSED, env="e1")
    hub.chunks[_REFUSED] = ChunkState(chunk_id=_REFUSED, status=ChunkStatus.RUNNING, route_runner_id="other-runner")
    lease = store.active_lease(lease_id)
    assert lease is not None

    Attempt(ctx, lease).escalate_harness_version_refused(
        LaunchVersionRefusedError(CLAUDE_CODE_HARNESS_ID, _REFUSAL), via="test"
    )

    assert store.active_lease(lease_id) is None
    assert _closures(store, _REFUSED) == [("released", None)]
    assert store.open_escalation_for_chunk(_REFUSED) is None


def test_a_refusal_under_the_runners_brake_defers(tmp_path: Path) -> None:
    store, _hub, _harness, ctx = _rig(tmp_path)
    lease_id = _seed_spawned(store, _REFUSED, env="e1")
    store.record_local_pause(
        paused=True,
        at=_NOW,
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED,
        report_payload=json.dumps({"runner_id": "r1", "by": "operator"}),
    )
    lease = store.active_lease(lease_id)
    assert lease is not None

    Attempt(replace(ctx), lease).escalate_harness_version_refused(
        LaunchVersionRefusedError(CLAUDE_CODE_HARNESS_ID, _REFUSAL), via="test"
    )

    assert store.active_lease(lease_id) is not None
    assert _closures(store, _REFUSED) == []
    assert store.open_escalation_for_chunk(_REFUSED) is None
