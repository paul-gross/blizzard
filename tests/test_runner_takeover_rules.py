"""The operator takeover's decisions, pinned by value — plain scopes, leases and chunk views, no
store, no harness, no clock (``blizzard.runner.lifecycle.takeover``)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.leases import Lease
from blizzard.runner.lifecycle.model import TakeoverHolds
from blizzard.runner.lifecycle.takeover import (
    TAKEOVER_TRANSITIONS,
    ChunkNotTakeable,
    LiveWorkerConflict,
    OpenTakeover,
    SubmissionPending,
    TakeoverCloseScope,
    TakeoverEndedElsewhere,
    TakeoverError,
    TakeoverOpenScope,
    TakeoverOwnerUnresolvable,
    TakeoverState,
    TakeoverVerb,
    admit_takeover,
    bounded_takeover_env,
    takeover_closing,
)
from blizzard.runner.node_steps.chunk_state import ChunkState

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
_BINDING = EnvBinding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _lease(*, lease_id: str = "lease_1", epoch: int = 3, session: bool = True) -> Lease:
    return Lease(
        lease_id=lease_id,
        chunk_id="ch_1",
        graph_id="g_1",
        node_id="nd_build",
        node_name="build",
        epoch=epoch,
        retries_max=2,
        created_at=_NOW,
        pid=100,
        process_start_time="start-100",
        session_id="sess-a" if session else None,
        harness_id="cc",
    )


def _takeover(  # type: ignore[no-untyped-def]
    *,
    takeover_id: str = "tko_1",
    reference_epoch: int | None = 3,
    fence_epoch: int | None = None,
    hold_epoch: int | None = None,
):
    return OpenTakeover(
        takeover_id=takeover_id,
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=fence_epoch,
        opened_at=_NOW,
        harness_id="cc",
        reference_epoch=reference_epoch,
        hold_epoch=hold_epoch,
    )


def _scope(**overrides: object) -> TakeoverOpenScope:
    fields: dict[str, object] = {
        "chunk_id": "ch_1",
        "open_takeover": None,
        "bindings": [_BINDING],
        "active_lease": None,
        "latest_lease_with_session": _lease(),
        "latest_epoch": 3,
        **overrides,
    }
    return TakeoverOpenScope(**fields)  # type: ignore[arg-type]


def _admit(scope: TakeoverOpenScope, *, force: bool = False, parked: bool = False, pending: bool = False):  # type: ignore[no-untyped-def]
    return admit_takeover(replace(scope, active_parked=parked, submission_pending=pending), force=force)


def test_admit_takeover_refusals() -> None:
    with pytest.raises(ChunkNotTakeable):
        _admit(_scope(open_takeover=_takeover()))
    with pytest.raises(ChunkNotTakeable):
        _admit(_scope(bindings=[]))
    with pytest.raises(LiveWorkerConflict):
        _admit(_scope(active_lease=_lease()))
    with pytest.raises(SubmissionPending):
        _admit(_scope(active_lease=_lease()), force=True, pending=True)
    with pytest.raises(ChunkNotTakeable):
        _admit(_scope(latest_lease_with_session=None))
    with pytest.raises(ChunkNotTakeable):
        _admit(_scope(latest_lease_with_session=_lease(session=False)))


def test_admitted_takeover_references_the_session_and_fences_a_live_worker() -> None:
    closed = _admit(_scope())
    assert (closed.reference.lease_id, closed.live, closed.fence_epoch, closed.workdir) == (
        "lease_1",
        False,
        None,
        "/ws/e1",
    )
    forced = _admit(_scope(active_lease=_lease(lease_id="lease_2", epoch=4), latest_epoch=4), force=True)
    assert (forced.reference.lease_id, forced.live, forced.fence_epoch) == ("lease_2", True, 5)
    parked = _admit(_scope(active_lease=_lease(lease_id="lease_2")), parked=True, pending=True)
    assert (parked.reference.lease_id, parked.live, parked.fence_epoch) == ("lease_2", False, None)


def test_a_takeover_holds_the_chunks_latest_epoch_above_a_sessionless_mint() -> None:
    """A sessionless escalation mint at epoch 4 follows the session-bearing lease at 3: the
    takeover references 3 yet still holds the chunk's own epoch 4, and only a later re-claim
    is the loop's again."""
    admitted = _admit(_scope(latest_lease_with_session=_lease(epoch=3), latest_epoch=4))
    assert (admitted.reference.epoch, admitted.fence_epoch, admitted.hold_epoch) == (3, None, 4)
    takeover = _takeover(reference_epoch=admitted.reference.epoch, hold_epoch=admitted.hold_epoch)
    assert takeover.holds("ch_1", 4)
    assert not takeover.holds("ch_1", 5)


def test_live_counts_exited_eliciting_backing_off() -> None:
    # An active lease that is not parked is live whatever its worker is doing, since it can still land a
    # verdict; only a park makes it not live.
    with pytest.raises(LiveWorkerConflict):
        _admit(_scope(active_lease=_lease()), parked=False)
    assert not _admit(_scope(active_lease=_lease()), parked=True).live


def test_takeover_refused_while_requeue_pending() -> None:
    with pytest.raises(ChunkNotTakeable, match="requeue pending"):
        _admit(_scope(requeue_pending=True))
    with pytest.raises(ChunkNotTakeable, match="requeue pending"):
        _admit(_scope(requeue_pending=True, active_lease=_lease()), force=True)


def test_bounded_takeover_env() -> None:
    full = {
        "BLIZZARD_LEASE_ID": "lease_1",
        "BLIZZARD_LEASE_TOKEN": "tok",
        "PATH": "/usr/bin",
        "HOME": "/home/op",
        "TERM": "xterm",
        "ANTHROPIC_API_KEY": "secret",
    }
    assert bounded_takeover_env(full) == {
        "BLIZZARD_LEASE_ID": "lease_1",
        "BLIZZARD_LEASE_TOKEN": "tok",
        "PATH": "/usr/bin",
        "HOME": "/home/op",
    }


def test_takeover_closing() -> None:
    open_one = _takeover()
    assert takeover_closing(TakeoverCloseScope(chunk_id="ch_1", open_takeover=open_one), "tko_1") is open_one
    assert takeover_closing(TakeoverCloseScope(chunk_id="ch_1", open_takeover=None), "tko_1") is None
    with pytest.raises(TakeoverEndedElsewhere):
        takeover_closing(TakeoverCloseScope(chunk_id="ch_1", open_takeover=open_one), "tko_other")


def test_takeover_transitions() -> None:
    assert set(TAKEOVER_TRANSITIONS) == set(TakeoverState)
    for verbs in TAKEOVER_TRANSITIONS.values():
        assert set(verbs) == set(TakeoverVerb)


def test_owner_unresolvable_is_takeover_error() -> None:
    assert issubclass(TakeoverOwnerUnresolvable, TakeoverError)


def test_ended_by_terminal_view() -> None:
    takeover = _takeover()
    for status in (ChunkStatus.DONE, ChunkStatus.STOPPED):
        assert takeover.ended_by(ChunkState(chunk_id="ch_1", status=status))
    for status in (ChunkStatus.RUNNING, ChunkStatus.NEEDS_HUMAN, ChunkStatus.READY):
        assert not takeover.ended_by(ChunkState(chunk_id="ch_1", status=status))


def test_takeover_skip_scoped_to_reference_epoch() -> None:
    takeover = _takeover(reference_epoch=3)
    assert takeover.holds("ch_1", 3)
    assert takeover.holds("ch_1", 2)
    assert not takeover.holds("ch_1", 4)  # a lease a later re-claim minted
    assert not takeover.holds("ch_2", 3)
    forced = _takeover(reference_epoch=3, fence_epoch=4)
    assert forced.holds("ch_1", 4)
    assert not forced.holds("ch_1", 5)
    # A takeover recording neither epoch holds the whole chunk.
    assert _takeover(reference_epoch=None).holds("ch_1", 9)
    holds = TakeoverHolds.of([takeover])
    assert holds.holds_lease(_lease(epoch=3))
    assert not holds.holds_lease(_lease(epoch=4))
