"""`DormantSession.on_unpause` and `pause_park_settled` — every argument each hands the seam it calls.

Unit tier over a real store, with recording spies at the decision and effect seams the two methods call
(`unpause_move`, `releases_usage_limited`, the suppression read, `_wake`, the usage recorder, the elicitation
files): the pause-park scenarios in `tests/test_chunk_pause.py` and `tests/test_elicitation_launch_collect.py` prove the
outcomes, and this pins what is *passed*, so a dropped, swapped or defaulted argument fails here."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle import dormant
from blizzard.runner.lifecycle.dormant import DormantSession, PauseParkSettle
from blizzard.runner.lifecycle.model import UnpauseMove
from blizzard.runner.lifecycle.spawn import Spawner, Suppression, SuppressionCause
from blizzard.runner.node_steps.chunk_state import ChunkPause, ChunkState
from blizzard.runner.throttle.gate import StartKind
from blizzard.runner.throttle.pause import PausePark, RunnerBrakes
from blizzard.runner.throttle.settings import HoldOrRelease, ThrottleSettings
from blizzard.runner.usage.recorder import UsageRecorder
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


class _Calls(list[tuple[tuple[Any, ...], dict[str, Any]]]):
    """Each call's `(args, kwargs)`, in order."""


def _spy(monkeypatch: pytest.MonkeyPatch, owner: Any, name: str, result: Any = None, *, bound: bool = False) -> _Calls:
    """Replace `owner.name` with a recorder returning `result`; `bound` drops the leading `self` of a class-level patch."""
    calls = _Calls()

    def recorder(*args: Any, **kwargs: Any) -> Any:
        calls.append((args[1:] if bound else args, kwargs))
        return result

    monkeypatch.setattr(owner, name, recorder)
    return calls


def _store(tmp_path: Path):
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _rig(tmp_path: Path, *, with_session: bool = True, with_binding: bool = True, probe: FakeProbe | None = None):
    """A pause-parked lease whose worker is gone, its chunk routed to this runner and no longer paused."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    if with_session:
        store.record_spawn(
            "lease_1",
            pid=100,
            process_start_time="start-100",
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
            spawned_at=_NOW,
            pgid=100,
        )
    if with_binding:
        store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkState(
        chunk_id="ch_1", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id=FIXTURE_RUNNER_ID
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=probe or FakeProbe(),
        clock=FixedClock(_NOW),
    )
    lease = store.active_lease("lease_1")
    assert lease is not None
    park = store.open_pause_parks()["lease_1"]
    return SimpleNamespace(
        store=store,
        hub=hub,
        harness=harness,
        ctx=ctx,
        lease=lease,
        park=park,
        bindings=store.bindings_for_chunk("ch_1"),
    )


def _unpause(rig, *, elicitation=None, ask_parked: bool = False, brakes=None, bindings=None):
    DormantSession(rig.ctx, rig.lease).on_unpause(
        rig.park,
        elicitation,
        bindings=rig.bindings if bindings is None else bindings,
        ask_parked=ask_parked,
        brakes=brakes if brakes is not None else Spawner(rig.ctx).brakes(),
    )


def _elicitation(rig, *, epoch: int = 1):
    rig.store.record_elicitation_launch("lease_1", 1, output_path="/tmp/unused-out", at=_NOW)
    standing = rig.store.in_flight_elicitation("lease_1", 1)
    assert standing is not None
    return replace(standing, epoch=epoch)


# --- on_unpause: the reads that decide the move -------------------------------------------------------


def test_the_start_suppression_is_read_as_a_pause_resume_continuation_for_this_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    suppression = _spy(monkeypatch, Spawner, "suppression", None, bound=True)
    _spy(monkeypatch, dormant, "unpause_move", UnpauseMove.WAIT)
    brakes = Spawner(rig.ctx).brakes()

    _unpause(rig, brakes=brakes)

    assert suppression == [
        (
            (),
            {
                "start": StartKind.CONTINUATION,
                "via": "pause-resume",
                "chunk_id": "ch_1",
                "lease_id": "lease_1",
                "brakes": brakes,
            },
        )
    ]


def test_an_unregistered_runner_stays_parked_having_read_nothing_further(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    monkeypatch.setattr(dormant, "registered_runner_id", lambda identity: None)
    suppression = _spy(monkeypatch, Spawner, "suppression", None, bound=True)
    resumes = _spy(monkeypatch, type(rig.ctx.stores.pause), "record_pause_park_resume", bound=True)

    _unpause(rig)

    assert suppression == [] and resumes == []


@pytest.mark.parametrize(
    ("suppression", "ask_parked", "judge_standing", "with_binding", "with_session", "expected"),
    [
        (None, False, False, True, True, {"braked": False, "ask_parked": False, "judge_parked": False, "has": True}),
        (
            Suppression(SuppressionCause.BRAKED),
            False,
            False,
            True,
            True,
            {"braked": True, "ask_parked": False, "judge_parked": False, "has": True},
        ),
        (
            Suppression(SuppressionCause.DECLINED),
            False,
            False,
            True,
            True,
            {"braked": True, "ask_parked": False, "judge_parked": False, "has": True},
        ),
        (None, True, False, True, True, {"braked": False, "ask_parked": True, "judge_parked": False, "has": True}),
        (None, False, True, True, True, {"braked": False, "ask_parked": False, "judge_parked": True, "has": True}),
        (None, False, False, False, True, {"braked": False, "ask_parked": False, "judge_parked": False, "has": False}),
        (None, False, False, True, False, {"braked": False, "ask_parked": False, "judge_parked": False, "has": False}),
        (None, False, False, False, False, {"braked": False, "ask_parked": False, "judge_parked": False, "has": False}),
    ],
    ids=["clear", "braked", "declined", "ask-parked", "judge-parked", "no-binding", "no-session", "neither"],
)
def test_the_unpause_move_is_asked_about_the_view_runner_and_each_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suppression,
    ask_parked,
    judge_standing,
    with_binding,
    with_session,
    expected,
):
    rig = _rig(tmp_path, with_session=with_session, with_binding=with_binding)
    _spy(monkeypatch, Spawner, "suppression", suppression, bound=True)
    move = _spy(monkeypatch, dormant, "unpause_move", UnpauseMove.WAIT)
    elicitation = _elicitation(rig) if judge_standing else None

    _unpause(rig, elicitation=elicitation, ask_parked=ask_parked, bindings=rig.bindings if with_binding else [])

    assert len(move) == 1
    (args, kwargs) = move[0]
    assert args == (rig.hub.chunks["ch_1"], FIXTURE_RUNNER_ID)
    assert kwargs == {
        "braked": expected["braked"],
        "ask_parked": expected["ask_parked"],
        "judge_parked": expected["judge_parked"],
        "has_env_and_session": expected["has"],
    }


def test_an_elicitation_of_another_epoch_is_not_a_standing_judge_park(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _rig(tmp_path)
    _spy(monkeypatch, Spawner, "suppression", None, bound=True)
    move = _spy(monkeypatch, dormant, "unpause_move", UnpauseMove.WAIT)

    _unpause(rig, elicitation=_elicitation(rig, epoch=2))

    assert move[0][1]["judge_parked"] is False


def test_an_elicitation_the_settle_just_cleared_is_not_a_standing_judge_park(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    _spy(monkeypatch, Spawner, "suppression", None, bound=True)
    move = _spy(monkeypatch, dormant, "unpause_move", UnpauseMove.WAIT)
    _spy(monkeypatch, DormantSession, "pause_park_settled", PauseParkSettle.CLEARED, bound=True)

    _unpause(rig, elicitation=_elicitation(rig))

    assert move[0][1]["judge_parked"] is False


def test_the_settle_is_asked_about_the_park_the_elicitation_and_the_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    settle = _spy(monkeypatch, DormantSession, "pause_park_settled", PauseParkSettle.UNSETTLED, bound=True)
    elicitation = _elicitation(rig)

    _unpause(rig, elicitation=elicitation)

    assert settle == [((rig.park, elicitation, rig.bindings), {})]


def test_an_unsettled_park_reads_nothing_and_moves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _rig(tmp_path)
    _spy(monkeypatch, DormantSession, "pause_park_settled", PauseParkSettle.UNSETTLED, bound=True)
    suppression = _spy(monkeypatch, Spawner, "suppression", None, bound=True)

    _unpause(rig)

    assert suppression == []


# --- on_unpause: the usage-limit release ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("paused", "ask_parked", "suppression", "operator_holds", "declined"),
    [
        (False, False, None, False, False),
        (True, False, None, True, False),
        (False, True, None, True, False),
        (False, False, Suppression(SuppressionCause.DECLINED), False, True),
        (False, False, Suppression(SuppressionCause.BRAKED), False, False),
    ],
    ids=["nothing", "chunk-paused", "ask-parked", "declined", "braked"],
)
def test_the_usage_limit_release_is_asked_with_the_park_and_both_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paused, ask_parked, suppression, operator_holds, declined
):
    rig = _rig(tmp_path)
    throttle = ThrottleSettings(on_usage_limit=HoldOrRelease.RELEASE)
    rig.ctx = replace(rig.ctx, config=replace(rig.ctx.config, throttle=throttle))
    if paused:
        rig.hub.chunks["ch_1"] = replace(
            rig.hub.chunks["ch_1"], pause=ChunkPause(by="operator", set_at="2026-07-16T12:00:00Z")
        )
    _spy(monkeypatch, Spawner, "suppression", suppression, bound=True)
    _spy(monkeypatch, dormant, "unpause_move", UnpauseMove.WAIT)
    release = _spy(monkeypatch, dormant, "releases_usage_limited", False)

    _unpause(rig, ask_parked=ask_parked)

    assert release == [((throttle, rig.park), {"operator_holds": operator_holds, "declined": declined})]


def test_a_chunk_routed_elsewhere_is_never_offered_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _rig(tmp_path)
    rig.hub.chunks["ch_1"] = replace(rig.hub.chunks["ch_1"], route_runner_id="someone-else")
    _spy(monkeypatch, Spawner, "suppression", None, bound=True)
    _spy(monkeypatch, dormant, "unpause_move", UnpauseMove.WAIT)
    release = _spy(monkeypatch, dormant, "releases_usage_limited", True)
    offered = _spy(monkeypatch, DormantSession, "_offer_usage_limited", bound=True)

    _unpause(rig)

    assert release == [] and offered == []


def test_a_released_usage_limited_park_is_offered_with_its_bindings_and_moves_no_further(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    _spy(monkeypatch, Spawner, "suppression", None, bound=True)
    move = _spy(monkeypatch, dormant, "unpause_move", UnpauseMove.WAIT)
    _spy(monkeypatch, dormant, "releases_usage_limited", True)
    offered = _spy(monkeypatch, DormantSession, "_offer_usage_limited", bound=True)

    _unpause(rig)

    assert offered == [((rig.bindings,), {})]
    assert move == []


# --- on_unpause: each move's effects ----------------------------------------------------------------------


def _moving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, move):
    rig = _rig(tmp_path)
    _spy(monkeypatch, Spawner, "suppression", None, bound=True)
    _spy(monkeypatch, dormant, "unpause_move", move)
    _spy(monkeypatch, dormant, "releases_usage_limited", False)
    rig.resumes = _spy(monkeypatch, type(rig.ctx.stores.pause), "record_pause_park_resume", bound=True)
    rig.wake = _spy(monkeypatch, DormantSession, "_wake", (4321, _NOW), bound=True)
    rig.worker_usage = _spy(monkeypatch, UsageRecorder, "record_worker", bound=True)
    return rig


def test_waiting_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.WAIT)

    _unpause(rig)

    assert (rig.resumes, rig.wake, rig.worker_usage) == ([], [], [])


def test_clearing_for_an_answer_records_the_resume_for_this_lease_at_now_and_wakes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.CLEAR_AWAIT_ANSWER)

    _unpause(rig, ask_parked=True)

    assert rig.resumes == [((), {"lease_id": "lease_1", "resumed_at": _NOW})]
    assert (rig.wake, rig.worker_usage) == ([], [])


def test_relaunching_the_judge_passes_now_and_the_standing_launch_instant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.RELAUNCH_JUDGE)
    relaunch = _spy(monkeypatch, DormantSession, "resume_judge_usage_limit_park", bound=True)
    elicitation = _elicitation(rig)

    _unpause(rig, elicitation=elicitation)

    assert relaunch == [((_NOW,), {"superseded_invocation": iso_utc(elicitation.first_launched_at)})]
    assert (rig.resumes, rig.wake) == ([], [])


def test_a_session_with_no_warm_env_cannot_resume_and_records_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.CANNOT_RESUME)

    _unpause(rig)

    assert (rig.resumes, rig.wake, rig.worker_usage) == ([], [], [])


def test_waking_books_the_paused_generation_then_wakes_with_the_unpause_message_and_records_the_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.WAKE)
    resolved = _spy(monkeypatch, DormantSession, "_resolve_harness", rig.harness, bound=True)

    _unpause(rig)

    assert resolved == [((), {"via": "unpause-resume"})]
    assert rig.worker_usage == [((rig.lease, rig.bindings), {})]
    assert rig.wake == [((dormant.UNPAUSE_MESSAGE, rig.bindings), {"harness": rig.harness, "at": _NOW})]
    assert rig.resumes == [((), {"lease_id": "lease_1", "resumed_at": _NOW})]


def test_an_unresolvable_owner_wakes_nothing_and_books_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.WAKE)
    _spy(monkeypatch, DormantSession, "_resolve_harness", None, bound=True)

    _unpause(rig)

    assert (rig.resumes, rig.wake, rig.worker_usage) == ([], [], [])


# --- pause_park_settled -------------------------------------------------------------------------------------


def _settle(rig, elicitation=None):
    return DormantSession(rig.ctx, rig.lease).pause_park_settled(rig.park, elicitation, rig.bindings)


def test_the_workers_group_is_judged_settled_by_its_own_identity_and_the_drain_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    group = _spy(monkeypatch, DormantSession, "_owned_group_settled", False, bound=True)

    assert _settle(rig) is PauseParkSettle.UNSETTLED
    assert group == [((), {"pid": 100, "process_start_time": "start-100", "pgid": 100, "past_deadline": False})]


def test_a_park_older_than_the_drain_budget_judges_its_group_past_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    rig.ctx.clock.advance(timedelta(hours=1))
    group = _spy(monkeypatch, DormantSession, "_owned_group_settled", False, bound=True)

    _settle(rig)

    assert group[0][1]["past_deadline"] is True


def test_a_settled_worker_with_no_named_elicitation_settles_leaving_any_standing_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    group = _spy(monkeypatch, DormantSession, "_owned_group_settled", True, bound=True)
    unnamed = _elicitation(rig)  # the park names no elicitation: a usage-limit judge park's record

    assert _settle(rig, unnamed) is PauseParkSettle.SETTLED
    assert len(group) == 1
    assert rig.store.in_flight_elicitation("lease_1", 1) is not None


def _park_naming(rig, elicitation):
    rig.park = PausePark(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW, interrupted_elicitation_id=elicitation.id)


def test_a_named_elicitation_is_judged_settled_by_its_own_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _rig(tmp_path)
    elicitation = replace(_elicitation(rig), pid=7, process_start_time="start-7", pgid=8)
    _park_naming(rig, elicitation)
    answers = iter([True, False])
    group = _spy(monkeypatch, DormantSession, "_owned_group_settled", None, bound=True)
    monkeypatch.setattr(
        DormantSession, "_owned_group_settled", lambda self, **kw: (group.append(((), kw)), next(answers))[1]
    )

    assert _settle(rig, elicitation) is PauseParkSettle.UNSETTLED
    assert group[-1] == ((), {"pid": 7, "process_start_time": "start-7", "pgid": 8, "past_deadline": False})
    assert rig.store.in_flight_elicitation("lease_1", 1) is not None


def test_a_named_elicitation_that_exited_books_its_judge_usage_then_clears_and_sweeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    elicitation = replace(_elicitation(rig), pid=7, process_start_time="start-7", pgid=8, relaunch_count=3)
    _park_naming(rig, elicitation)
    _spy(monkeypatch, DormantSession, "_owned_group_settled", True, bound=True)
    resolved = _spy(monkeypatch, DormantSession, "_resolve_harness_for_usage_only", rig.harness, bound=True)
    monkeypatch.setattr(type(rig.ctx.elicitation_files), "read", lambda self, path: f"read:{path}")
    usage = _spy(monkeypatch, UsageRecorder, "record_attempt", bound=True)
    cleared = _spy(monkeypatch, type(rig.ctx.stores.elicitations), "clear_elicitation", bound=True)
    swept = _spy(monkeypatch, type(rig.ctx.elicitation_files), "cleanup", bound=True)

    assert _settle(rig, elicitation) is PauseParkSettle.CLEARED

    assert resolved == [((), {"via": "pause-park-elicitation-usage"})]
    assert usage == [((rig.lease, rig.bindings), {"judge_output": "read:/tmp/unused-out"})]
    assert cleared == [(("lease_1", 1), {})]
    assert swept == [(("lease_1", 1), {"through_attempt": 3})]


def test_an_unresolvable_owner_still_clears_the_named_elicitation_without_booking_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    elicitation = replace(_elicitation(rig), pid=7, process_start_time="start-7", pgid=8)
    _park_naming(rig, elicitation)
    _spy(monkeypatch, DormantSession, "_owned_group_settled", True, bound=True)
    _spy(monkeypatch, DormantSession, "_resolve_harness_for_usage_only", None, bound=True)
    usage = _spy(monkeypatch, UsageRecorder, "record_attempt", bound=True)

    assert _settle(rig, elicitation) is PauseParkSettle.CLEARED
    assert usage == []
    assert rig.store.in_flight_elicitation("lease_1", 1) is None


def test_the_brakes_type_is_the_one_the_unpause_reads_from(tmp_path: Path):
    assert isinstance(Spawner(_rig(tmp_path).ctx).brakes(), RunnerBrakes)


# --- the log lines each outcome leaves ------------------------------------------------------------------------


def _logged(logs, event):
    return [{k: v for k, v in entry.items() if k != "timestamp"} for entry in logs if entry["event"] == event]


def test_clearing_for_an_answer_logs_the_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.CLEAR_AWAIT_ANSWER)

    with capture_logs() as logs:
        _unpause(rig, ask_parked=True)

    assert _logged(logs, "pause lifted on an ask-parked chunk — awaiting its answer") == [
        {"event": "pause lifted on an ask-parked chunk — awaiting its answer", "log_level": "info", "chunk_id": "ch_1"}
    ]


def test_a_session_that_cannot_resume_warns_with_the_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.CANNOT_RESUME)

    with capture_logs() as logs:
        _unpause(rig)

    assert _logged(logs, "unpaused chunk has no warm env/session — cannot resume") == [
        {
            "event": "unpaused chunk has no warm env/session — cannot resume",
            "log_level": "warning",
            "chunk_id": "ch_1",
        }
    ]


def test_a_woken_session_logs_its_chunk_lease_epoch_and_new_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.WAKE)
    _spy(monkeypatch, DormantSession, "_resolve_harness", rig.harness, bound=True)

    with capture_logs() as logs:
        _unpause(rig)

    assert _logged(logs, "resumed dormant session after an operator unpause") == [
        {
            "event": "resumed dormant session after an operator unpause",
            "log_level": "info",
            "chunk_id": "ch_1",
            "lease_id": "lease_1",
            "epoch": 1,
            "pid": 4321,
        }
    ]


def test_a_cleared_elicitation_logs_its_chunk_lease_and_epoch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rig = _rig(tmp_path)
    elicitation = replace(_elicitation(rig), pid=7, process_start_time="start-7", pgid=8)
    _park_naming(rig, elicitation)
    _spy(monkeypatch, DormantSession, "_owned_group_settled", True, bound=True)
    _spy(monkeypatch, DormantSession, "_resolve_harness_for_usage_only", None, bound=True)

    with capture_logs() as logs:
        _settle(rig, elicitation)

    assert _logged(logs, "interrupted elicitation recorded and cleared under the pause park") == [
        {
            "event": "interrupted elicitation recorded and cleared under the pause park",
            "log_level": "info",
            "chunk_id": "ch_1",
            "lease_id": "lease_1",
            "epoch": 1,
        }
    ]


def test_a_standing_elicitation_does_not_relaunch_the_judge_unless_the_move_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.WAKE)
    _spy(monkeypatch, DormantSession, "_resolve_harness", rig.harness, bound=True)
    relaunch = _spy(monkeypatch, DormantSession, "resume_judge_usage_limit_park", bound=True)

    _unpause(rig, elicitation=_elicitation(rig))

    assert relaunch == []
    assert len(rig.wake) == 1


def test_a_judge_relaunch_move_with_no_standing_elicitation_falls_through_to_the_wake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _moving(tmp_path, monkeypatch, UnpauseMove.RELAUNCH_JUDGE)
    _spy(monkeypatch, DormantSession, "_resolve_harness", rig.harness, bound=True)
    relaunch = _spy(monkeypatch, DormantSession, "resume_judge_usage_limit_park", bound=True)

    _unpause(rig, elicitation=None)

    assert relaunch == []
    assert len(rig.wake) == 1
