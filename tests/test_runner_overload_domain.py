"""Provider-overload backoff policy and closure — pure domain (blizzard#595).

``backoff_delay`` is a pure formula; ``backing_off_facts`` closes a fact against a lease's
own current generation or elicitation launch instant, with no store of its own — both
exercised here against minimal in-memory fakes, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.elicitation import ElicitationRecord
from blizzard.runner.domain.overload import (
    BACKOFF_CAP_SECONDS,
    BACKOFF_LIMIT,
    OverloadFactRecord,
    backing_off_facts,
    backoff_delay,
)

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
    """The policy's own promise (D6): every ordinal short of the fall-through stays under
    the cap, so the cap is dead code short of a future policy change to the limit."""
    for streak_ordinal in range(1, BACKOFF_LIMIT):
        assert backoff_delay(streak_ordinal) < timedelta(seconds=BACKOFF_CAP_SECONDS)


@dataclass
class _FakeOverloadReads:
    facts: list[OverloadFactRecord]

    def overload_streak(self, lease_id: str, epoch: int) -> int:
        raise NotImplementedError  # unused by backing_off_facts

    def open_overload_facts(self) -> list[OverloadFactRecord]:
        return self.facts


@dataclass
class _FakeLeaseGeneration:
    generations: dict[str, int]

    def lease_generation(self, lease_id: str) -> int:
        return self.generations[lease_id]


@dataclass
class _FakeElicitations:
    records: dict[tuple[str, int], ElicitationRecord]

    def in_flight_elicitation(self, lease_id: str, epoch: int) -> ElicitationRecord | None:
        return self.records.get((lease_id, epoch))

    def in_flight_elicitation_lease_ids(self) -> set[str]:
        raise NotImplementedError  # unused by backing_off_facts


def _worker_fact(**overrides: object) -> OverloadFactRecord:
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
    return OverloadFactRecord(**fields)  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_worker_fact_stands_while_the_generation_is_unmoved() -> None:
    facts = _FakeOverloadReads([_worker_fact()])
    liveness = _FakeLeaseGeneration({"lease_1": 2})
    elicitations = _FakeElicitations({})
    result = backing_off_facts(facts, liveness, elicitations)
    assert result == {"lease_1": facts.facts[0]}


@pytest.mark.unit
def test_a_worker_fact_closes_once_the_generation_moves_past_it() -> None:
    """D7: no separate closing write — the recorded generation no longer matching the
    lease's own current one is itself the closure, e.g. after this exact backoff's wake."""
    facts = _FakeOverloadReads([_worker_fact(generation=2, invocation_identity="2")])
    liveness = _FakeLeaseGeneration({"lease_1": 3})
    elicitations = _FakeElicitations({})
    assert backing_off_facts(facts, liveness, elicitations) == {}


def _judge_fact(**overrides: object) -> OverloadFactRecord:
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
    return OverloadFactRecord(**fields)  # type: ignore[arg-type]


def _elicitation(*, first_launched_at: datetime) -> ElicitationRecord:
    return ElicitationRecord(
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
    facts = _FakeOverloadReads([_judge_fact()])
    liveness = _FakeLeaseGeneration({})
    elicitations = _FakeElicitations({("lease_1", 1): _elicitation(first_launched_at=_NOW)})
    assert backing_off_facts(facts, liveness, elicitations) == {"lease_1": facts.facts[0]}


@pytest.mark.unit
def test_a_judge_fact_closes_once_a_fresh_elicitation_relaunches_under_a_new_identity() -> None:
    facts = _FakeOverloadReads([_judge_fact()])
    liveness = _FakeLeaseGeneration({})
    later = _NOW + timedelta(minutes=5)
    elicitations = _FakeElicitations({("lease_1", 1): _elicitation(first_launched_at=later)})
    assert backing_off_facts(facts, liveness, elicitations) == {}


@pytest.mark.unit
def test_a_judge_fact_closes_once_no_elicitation_is_in_flight_at_all() -> None:
    facts = _FakeOverloadReads([_judge_fact()])
    liveness = _FakeLeaseGeneration({})
    elicitations = _FakeElicitations({})
    assert backing_off_facts(facts, liveness, elicitations) == {}


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
