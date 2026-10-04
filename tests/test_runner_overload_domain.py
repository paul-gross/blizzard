"""Provider-overload backoff policy and closure — pure domain.

``backoff_delay`` is a pure formula and :meth:`OverloadExit.still_open` closes a fact against a
lease's own current generation or elicitation launch instant — both pinned by value.
``backing_off_facts``, the bulk read that applies the predicate, runs against minimal in-memory
fakes, no I/O."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.leases import Lease
from blizzard.runner.leases.elicitation import PendingElicitation
from blizzard.runner.leases.overload import (
    BACKOFF_CAP_SECONDS,
    BACKOFF_LIMIT,
    OverloadExit,
    backing_off_facts,
    backoff_delay,
)
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.throttle.overload import classify_judge_overload, classify_worker_overload
from tests.runner_fakes import FakeHarness

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("streak_ordinal", "expected_seconds"),
    [(1, 60), (2, 120), (3, 240), (4, 480)],
)
def test_backoff_delay_doubles_each_consecutive_ordinal(streak_ordinal: int, expected_seconds: int) -> None:
    assert backoff_delay(streak_ordinal) == timedelta(seconds=expected_seconds)


@pytest.mark.unit
def test_backoff_delay_caps_rather_than_keeps_doubling() -> None:
    """A streak position past the cap (never actually reached at ``BACKOFF_LIMIT`` — the
    streak falls through first) still reads as the capped delay, not an ever-growing one."""
    assert backoff_delay(10) == timedelta(seconds=BACKOFF_CAP_SECONDS)


@pytest.mark.unit
def test_backoff_delay_never_reaches_the_cap_within_the_limit() -> None:
    """The policy's own promise: every ordinal short of the fall-through stays under
    the cap, so the cap is dead code short of a future policy change to the limit."""
    for streak_ordinal in range(1, BACKOFF_LIMIT):
        assert backoff_delay(streak_ordinal) < timedelta(seconds=BACKOFF_CAP_SECONDS)


@dataclass
class _FakeOverloadReads:
    facts: list[OverloadExit]

    def overload_streak(self, lease_id: str, epoch: int) -> int:
        raise NotImplementedError  # unused by backing_off_facts

    def open_overload_facts(self) -> list[OverloadExit]:
        return self.facts


@dataclass
class _FakeLeaseGeneration:
    generations: dict[str, int]

    def lease_generation(self, lease_id: str) -> int:
        return self.generations[lease_id]

    def lease_generations(self, lease_ids: Sequence[str]) -> dict[str, int]:
        return {lease_id: self.generations[lease_id] for lease_id in lease_ids if lease_id in self.generations}


@dataclass
class _FakeElicitations:
    records: dict[tuple[str, int], PendingElicitation]

    def in_flight_elicitation(self, lease_id: str, epoch: int) -> PendingElicitation | None:
        return self.records.get((lease_id, epoch))

    def in_flight_elicitations(self, pairs: Sequence[tuple[str, int]]) -> dict[tuple[str, int], PendingElicitation]:
        return {pair: self.records[pair] for pair in pairs if pair in self.records}

    def in_flight_elicitation_lease_ids(self) -> set[str]:
        raise NotImplementedError  # unused by backing_off_facts

    def in_flight_elicitations_by_lease(self) -> dict[str, PendingElicitation]:
        raise NotImplementedError  # unused by backing_off_facts


def _worker_fact(**overrides: object) -> OverloadExit:
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "epoch": 1,
        "generation": 2,
        "invocation_kind": "worker",
        "invocation_identity": "2",
        "streak_ordinal": 1,
        "observed_at": _NOW,
        "resume_after": _NOW + timedelta(seconds=60),
    }
    fields.update(overrides)
    return OverloadExit(**fields)  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_worker_fact_stands_while_the_generation_is_unmoved() -> None:
    assert _worker_fact().still_open(generation=2, elicitation_launched_at=None)


@pytest.mark.unit
def test_a_worker_fact_closes_once_the_generation_moves_past_it() -> None:
    """No separate closing write — the recorded generation no longer matching the
    lease's own current one is itself the closure, e.g. after this exact backoff's wake."""
    assert not _worker_fact(generation=2, invocation_identity="2").still_open(
        generation=3, elicitation_launched_at=None
    )


def _judge_fact(**overrides: object) -> OverloadExit:
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "epoch": 1,
        "generation": 1,
        "invocation_kind": "judge",
        "invocation_identity": iso_utc(_NOW),
        "streak_ordinal": 1,
        "observed_at": _NOW,
        "resume_after": _NOW + timedelta(seconds=60),
    }
    fields.update(overrides)
    return OverloadExit(**fields)  # type: ignore[arg-type]


def _elicitation(*, first_launched_at: datetime) -> PendingElicitation:
    return PendingElicitation(
        id=1,
        lease_id="lease_1",
        epoch=1,
        pid=200,
        process_start_time="start-200",
        pgid=200,
        output_path="/tmp/out",
        first_launched_at=first_launched_at,
        relaunch_count=0,
    )


@pytest.mark.unit
def test_a_judge_fact_stands_while_its_own_elicitation_is_still_in_flight() -> None:
    assert _judge_fact().still_open(generation=None, elicitation_launched_at=_NOW)


@pytest.mark.unit
def test_a_judge_fact_closes_once_a_fresh_elicitation_relaunches_under_a_new_identity() -> None:
    later = _NOW + timedelta(minutes=5)
    assert not _judge_fact().still_open(generation=None, elicitation_launched_at=later)


@pytest.mark.unit
def test_a_judge_fact_closes_once_no_elicitation_is_in_flight_at_all() -> None:
    assert not _judge_fact().still_open(generation=None, elicitation_launched_at=None)


@pytest.mark.unit
def test_multiple_open_facts_key_the_result_by_lease_id() -> None:
    facts = _FakeOverloadReads(
        [
            _worker_fact(lease_id="lease_1", generation=2, invocation_identity="2"),
            _worker_fact(lease_id="lease_2", generation=5, invocation_identity="5"),
        ]
    )
    liveness = _FakeLeaseGeneration({"lease_1": 2, "lease_2": 5})
    elicitations = _FakeElicitations({})
    result = backing_off_facts(facts, liveness, elicitations)
    assert set(result) == {"lease_1", "lease_2"}


@pytest.mark.unit
def test_the_bulk_read_drops_every_fact_the_predicate_closes() -> None:
    """Each fact is judged against its own lease's bulk-read generation or elicitation."""
    worker_open = _worker_fact(lease_id="lease_1", generation=2, invocation_identity="2")
    worker_moved = _worker_fact(lease_id="lease_2", generation=5, invocation_identity="5")
    judge_open = _judge_fact(lease_id="lease_3")
    judge_relaunched = _judge_fact(lease_id="lease_4")
    facts = _FakeOverloadReads([worker_open, worker_moved, judge_open, judge_relaunched])
    liveness = _FakeLeaseGeneration({"lease_1": 2, "lease_2": 6})
    elicitations = _FakeElicitations(
        {
            ("lease_3", 1): _elicitation(first_launched_at=_NOW),
            ("lease_4", 1): _elicitation(first_launched_at=_NOW + timedelta(minutes=5)),
        }
    )
    assert backing_off_facts(facts, liveness, elicitations) == {"lease_1": worker_open, "lease_3": judge_open}


@pytest.mark.unit
@pytest.mark.parametrize("classify", [classify_worker_overload, classify_judge_overload])
def test_overload_is_classified_by_the_sessions_own_harness(
    classify: Callable[[LoopContext, Lease, str, Sequence[str]], ProviderOverload | None],
) -> None:
    overload = ProviderOverload(detail="529")
    handle = WorkerHandle(session_id="s", pid=1, process_start_time="start", pgid=1)
    registry = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=FakeHarness(handle=handle, verdict=None, overload=overload)),
            OPENCODE_HARNESS_ID: HarnessBinding(adapter=FakeHarness(handle=handle, verdict=None)),
        }
    )
    ctx = cast(LoopContext, SimpleNamespace(harnesses=registry))

    def on(harness_id: str) -> Lease:
        return cast(Lease, SimpleNamespace(session=SessionReference(harness_id, "s")))

    assert classify(ctx, on(CLAUDE_CODE_HARNESS_ID), "out", ["line"]) == overload
    assert classify(ctx, on(OPENCODE_HARNESS_ID), "out", ["line"]) is None
