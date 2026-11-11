"""``FlowStore`` (component tier). A submission takes the tenant's newest arrival plus one and is written once per
generation of a visit, only in a unit locking its chunk; a closure and a turn are written only in a unit locking the
submission's chunk and every flow it holds, and a closure only once, with its ``withdrawn`` facts; the standing read
projects a submission's own open turn, its busy flows, and the open submissions ahead of it; the open walk from a floor returns only open submissions at or above it; and ending a turn ``interrupted`` writes its
``turn-ended`` facts and, when asked, the closure, once. A turn gives its submission landing terms for each repository
it holds and has none for — an earlier generation's, else the candidate — so a retried turn and a resubmission keep
what the visit's first turn took."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, Table, func, select

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.delivery.flows import (
    FlowResource,
    LandingTerms,
    QueueStanding,
    Submission,
    Turn,
    TurnEvent,
    Withdrawal,
    flow_lock,
)
from blizzard.hub.domain.kernel.transactions import TransactionMisuse
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.transactions import HubTransactions
from tests.support.scaffolding import (
    build_hub,
    capture_statements,
    chunk_unit,
    flow_unit,
    hub_scoped,
    hub_store_connections,
    stamp,
)

_AT = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
_MAIN = FlowResource("repo_a", "main")
_OTHER = FlowResource("repo_b", "master")
_INTERRUPTED = TurnEvent(kind="turn-interrupted", chunk_id="ch_1", node_name="deliver", message="ended", detail={})


@dataclass(frozen=True)
class _World:
    engine: Engine
    flows: FlowStore

    def submit(
        self, chunk_id: str, *, generation: int = 0, epoch: int = 1, resources: Sequence[FlowResource] = (_MAIN,)
    ) -> Submission | None:
        """Submit ``chunk_id``'s ``nd_deliver`` visit at epoch 1 in a unit locking the chunk, as the reconcile does."""
        with chunk_unit(hub_store_connections(self.engine), chunk_id) as unit:
            return self.flows.submit(
                chunk_id,
                node_id="nd_deliver",
                epoch=epoch,
                generation=generation,
                resources=resources,
                at=_AT,
                transaction=unit,
            )

    def close(
        self, submission: Submission, outcome: Withdrawal, *, at: datetime = _AT
    ) -> tuple[FlowFactWritten, ...] | None:
        """Close ``submission`` in a unit locking its chunk and its flows."""
        with flow_unit(hub_store_connections(self.engine), submission) as unit:
            return self.flows.close(submission.submission_id, outcome=outcome, at=at, transaction=unit)

    def turn(self, submission: Submission, terms: Sequence[LandingTerms] = (), *, at: datetime = _AT) -> Turn:
        """Start ``submission``'s turn in a unit locking its chunk and its flows."""
        with flow_unit(hub_store_connections(self.engine), submission) as unit:
            return self.flows.take_turn(submission.submission_id, terms=terms, at=at, transaction=unit)

    def standing(self, submission: Submission) -> QueueStanding | None:
        """``submission``'s standing, read in a unit locking its chunk and its flows."""
        with flow_unit(hub_store_connections(self.engine), submission) as unit:
            return self.flows.queue_standing(submission.submission_id, transaction=unit)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    hub = build_hub(tmp_path)
    return _World(hub.engine, FlowStore(hub_store_connections(hub.engine), FixedClock(_AT)))


@pytest.fixture
def flows(world: _World) -> FlowStore:
    return world.flows


def _count(engine: Engine, table: Table) -> int:
    with engine.connect() as conn:
        return int(conn.execute(hub_scoped(select(func.count()).select_from(table))).scalar() or 0)


def _plant_turn(engine: Engine, submission_id: str, *, turn_id: str = "htn_1") -> None:
    with engine.begin() as conn:
        conn.execute(
            s.hub_turns.insert().values(
                stamp(s.hub_turns, turn_id=turn_id, submission_id=submission_id, started_at=_AT)
            )
        )


def test_each_submission_arrives_at_the_tenants_newest_plus_one(world: _World, flows: FlowStore) -> None:
    first = world.submit("ch_1")
    second = world.submit("ch_2", resources=[_OTHER])
    third = world.submit("ch_3", resources=[])

    assert first is not None and second is not None and third is not None
    assert (first.arrival, second.arrival, third.arrival) == (1, 2, 3)
    assert third.flows == ()


def test_a_submission_makes_its_flow_once_and_writes_a_submitted_fact_per_flow(world: _World, flows: FlowStore) -> None:
    first = world.submit("ch_1", resources=[_MAIN, _OTHER])
    second = world.submit("ch_2")

    assert first is not None and second is not None
    assert {f.resource for f in first.flows} == {"repo:repo_a/main", "repo:repo_b/master"}
    assert second.flows == tuple(f for f in first.flows if f.resource == "repo:repo_a/main")
    assert _count(world.engine, s.flows) == 2
    assert [(f.kind, f.flow_id) for f in flows.facts_of(second.submission_id)] == [
        ("submitted", second.flows[0].flow_id)
    ]


def test_a_second_submit_of_the_same_visit_generation_writes_nothing(world: _World, flows: FlowStore) -> None:
    first = world.submit("ch_1")

    again = world.submit("ch_1")

    assert first is not None and again is None
    assert _count(world.engine, s.flow_submissions) == 1
    assert _count(world.engine, s.flow_facts) == 1
    resubmitted = world.submit("ch_1", generation=1)
    assert resubmitted is not None and resubmitted.arrival == 2


def test_a_submission_refuses_a_unit_that_does_not_lock_its_chunk(world: _World, flows: FlowStore) -> None:
    with pytest.raises(TransactionMisuse), chunk_unit(hub_store_connections(world.engine), "ch_2") as unit:
        flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT, transaction=unit)

    assert _count(world.engine, s.flow_submissions) == 0


def test_a_submission_is_closed_once_with_one_withdrawn_fact_per_flow(world: _World, flows: FlowStore) -> None:
    submission = world.submit("ch_1", resources=[_MAIN, _OTHER])
    assert submission is not None

    assert world.close(submission, "superseded") is not None
    assert world.close(submission, "stopped", at=_AT + timedelta(seconds=1)) is None

    withdrawn = [f for f in flows.facts_of(submission.submission_id) if f.kind == "withdrawn"]
    assert sorted((f.flow_id, f.outcome) for f in withdrawn) == sorted(
        (flow.flow_id, "superseded") for flow in submission.flows
    )
    assert _count(world.engine, s.flow_submission_closures) == 1
    visit = flows.submission_for_visit("ch_1", node_id="nd_deliver", epoch=1)
    assert visit is not None and visit.closed == "superseded"


def test_a_close_or_turn_refuses_a_unit_missing_the_chunk_or_a_flow_key(world: _World, flows: FlowStore) -> None:
    submission = world.submit("ch_1", resources=[_MAIN, _OTHER])
    assert submission is not None
    store = hub_store_connections(world.engine)
    short = [flow_lock(submission.flows[0])]

    for chunks, keys in ((["ch_2"], [flow_lock(flow) for flow in submission.flows]), (["ch_1"], short)):
        with pytest.raises(TransactionMisuse), HubTransactions(store).begin() as transaction:
            unit = transaction.lock_set(chunks=chunks, keys=keys)
            flows.close(submission.submission_id, outcome="superseded", at=_AT, transaction=unit)
        with pytest.raises(TransactionMisuse), HubTransactions(store).begin() as transaction:
            unit = transaction.lock_set(chunks=chunks, keys=keys)
            flows.take_turn(submission.submission_id, terms=(), at=_AT, transaction=unit)

    assert _count(world.engine, s.flow_submission_closures) == 0
    assert _count(world.engine, s.hub_turns) == 0


def test_a_close_writes_even_with_a_turn_open_leaving_the_judgment_to_its_caller(
    world: _World, flows: FlowStore
) -> None:
    submission = world.submit("ch_1")
    assert submission is not None
    _plant_turn(world.engine, submission.submission_id)

    assert world.close(submission, "superseded") is not None
    assert _count(world.engine, s.flow_submission_closures) == 1


def test_the_standing_read_projects_the_turn_the_busy_flows_and_the_queue_ahead(
    world: _World, flows: FlowStore
) -> None:
    first = world.submit("ch_1")
    second = world.submit("ch_2", resources=[_MAIN, _OTHER])
    alone = world.submit("ch_3", resources=[])
    assert first is not None and second is not None and alone is not None

    assert world.standing(second) == QueueStanding(
        submission=second, turning=False, busy=False, ahead=frozenset({first.submission_id})
    )
    assert world.standing(alone) == QueueStanding(submission=alone, turning=False, busy=False, ahead=frozenset())

    _plant_turn(world.engine, first.submission_id)
    world.close(first, "superseded")

    standing = world.standing(second)
    assert standing is not None and (standing.turning, standing.busy, standing.ahead) == (False, True, frozenset())
    closed = world.standing(first)
    assert closed is not None and (closed.submission.closed, closed.turning) == ("superseded", True)


def test_the_floor_walk_returns_only_open_submissions_at_or_above_the_floor(world: _World, flows: FlowStore) -> None:
    submitted = [world.submit(f"ch_{n}") for n in range(1, 5)]
    assert all(submitted)
    ids = [sub.submission_id for sub in submitted if sub is not None]
    world.close(next(sub for sub in submitted if sub is not None and sub.submission_id == ids[2]), "completed")

    walked = flows.open_submissions_from(2)

    assert [sub.submission_id for sub in walked] == [ids[1], ids[3]]
    assert [sub.arrival for sub in walked] == [2, 4]
    assert all(sub.closed is None and len(sub.flows) == 1 for sub in walked)


def test_the_per_visit_lookup_answers_the_newest_generation(world: _World, flows: FlowStore) -> None:
    assert flows.submission_for_visit("ch_1", node_id="nd_deliver", epoch=1) is None
    world.submit("ch_1")
    newer = world.submit("ch_1", generation=1)

    visit = flows.submission_for_visit("ch_1", node_id="nd_deliver", epoch=1)

    assert visit == newer
    assert flows.submission_for_visit("ch_1", node_id="nd_deliver", epoch=2) is None


def test_ending_a_turn_interrupted_writes_its_facts_and_closure_once(world: _World, flows: FlowStore) -> None:
    submission = world.submit("ch_1")
    assert submission is not None
    _plant_turn(world.engine, submission.submission_id)
    assert [t.turn_id for t in flows.open_turns()] == ["htn_1"]

    assert flows.end_turn_interrupted("htn_1", withdrawal="stopped", event=_INTERRUPTED, at=_AT) is not None
    assert flows.end_turn_interrupted("htn_1", withdrawal="stopped", event=_INTERRUPTED, at=_AT) is None

    assert _count(world.engine, s.event_log) == 1
    assert flows.open_turns() == []
    assert [(f.kind, f.outcome) for f in flows.facts_of(submission.submission_id)] == [
        ("submitted", None),
        ("turn-ended", "interrupted"),
        ("withdrawn", "stopped"),
    ]
    assert flows.open_submissions_from(0) == []


def test_ending_a_turn_without_a_withdrawal_leaves_the_submission_open(world: _World, flows: FlowStore) -> None:
    submission = world.submit("ch_1")
    assert submission is not None
    _plant_turn(world.engine, submission.submission_id)

    assert flows.end_turn_interrupted("htn_1", withdrawal=None, event=_INTERRUPTED, at=_AT) is not None

    assert [sub.submission_id for sub in flows.open_submissions_from(0)] == [submission.submission_id]


_TERMS_A = LandingTerms("repo_a", "merge-on-checks", None, 1.0, True)
_TERMS_B = LandingTerms("repo_b", "merge-on-checks", None, 2.0)
_THIRD = FlowResource("repo_c", "main")


def _turn(world: _World, submission: Submission, terms: tuple[LandingTerms, ...], *, at: datetime = _AT) -> str:
    return world.turn(submission, terms, at=at).turn_id


def test_a_first_turn_takes_terms_for_every_repository_it_holds(world: _World, flows: FlowStore) -> None:
    submission = world.submit("ch_1", resources=[_MAIN, _OTHER])
    assert submission is not None
    assert flows.terms_of(submission.submission_id) == {}

    _turn(world, submission, (_TERMS_A, _TERMS_B))

    assert flows.terms_of(submission.submission_id) == {"repo_a": _TERMS_A, "repo_b": _TERMS_B}


def test_a_retried_turn_keeps_the_terms_its_first_attempt_took(world: _World, flows: FlowStore) -> None:
    submission = world.submit("ch_1")
    assert submission is not None
    first = _turn(world, submission, (_TERMS_A,))
    flows.end_turn_interrupted(first, withdrawal=None, event=_INTERRUPTED, at=_AT)

    _turn(world, submission, (LandingTerms("repo_a", "merge-on-checks", None, 9.0),))

    assert flows.terms_of(submission.submission_id) == {"repo_a": _TERMS_A}
    assert _count(world.engine, s.flow_submission_terms) == 1


def test_a_resubmission_copies_the_visits_terms_and_takes_fresh_ones_only_for_a_new_repository(
    world: _World,
    flows: FlowStore,
) -> None:
    first = world.submit("ch_1", resources=[_MAIN, _OTHER])
    assert first is not None
    turn = _turn(world, first, (_TERMS_A, _TERMS_B))
    flows.end_turn_interrupted(turn, withdrawal="superseded", event=_INTERRUPTED, at=_AT)
    second = world.submit("ch_1", generation=1, resources=[_MAIN, _THIRD])
    assert second is not None
    fresh_c = LandingTerms("repo_c", "merge-on-checks", None, None)

    _turn(world, second, (LandingTerms("repo_a", "merge-on-checks", None, 5.0), fresh_c))

    assert flows.terms_of(second.submission_id) == {"repo_a": _TERMS_A, "repo_c": fresh_c}


def test_the_first_turn_of_a_visit_is_kept_across_its_resubmissions(world: _World, flows: FlowStore) -> None:
    assert flows.first_turn_at("ch_1", node_id="nd_deliver", epoch=1) is None
    first = world.submit("ch_1")
    assert first is not None
    turn = _turn(world, first, (_TERMS_A,))
    flows.end_turn_interrupted(turn, withdrawal="superseded", event=_INTERRUPTED, at=_AT)
    second = world.submit("ch_1", generation=1)
    assert second is not None

    _turn(world, second, (_TERMS_A,), at=_AT + timedelta(hours=1))

    assert flows.first_turn_at("ch_1", node_id="nd_deliver", epoch=1) == _AT
    assert flows.first_turn_at("ch_1", node_id="nd_deliver", epoch=2) is None


def _plant_terms(engine: Engine, submission: Submission | None, repository_id: str, position: float) -> LandingTerms:
    assert submission is not None
    terms = LandingTerms(repository_id, "merge-on-checks", None, position)
    with engine.begin() as conn:
        conn.execute(
            s.flow_submission_terms.insert().values(
                stamp(
                    s.flow_submission_terms,
                    submission_id=submission.submission_id,
                    repository_id=repository_id,
                    landing_policy=terms.landing_policy,
                    park_timeout_hours=terms.park_timeout_hours,
                    landing_position=terms.landing_position,
                )
            )
        )
    return terms


def _close(engine: Engine, submission: Submission | None) -> None:
    assert submission is not None
    with engine.begin() as conn:
        conn.execute(
            s.flow_submission_closures.insert().values(
                stamp(
                    s.flow_submission_closures,
                    submission_id=submission.submission_id,
                    outcome="superseded",
                    closed_at=_AT,
                )
            )
        )


def test_each_repository_takes_the_terms_of_its_own_newest_earlier_generation(world: _World, flows: FlowStore) -> None:
    both = [_MAIN, _OTHER]
    zero = world.submit("ch_1", resources=both)
    older_b = _plant_terms(world.engine, zero, "repo_b", 10.0)
    _plant_terms(world.engine, zero, "repo_a", 11.0)
    _close(world.engine, zero)
    one = world.submit("ch_1", generation=1, resources=[_MAIN])
    newer_a = _plant_terms(world.engine, one, "repo_a", 12.0)
    _close(world.engine, one)
    # Another tenant-visible visit, another node epoch and a later generation all carry distinguishable terms that
    # must not win for the generation under turn.
    other_chunk = world.submit("ch_2", generation=1, resources=both)
    _plant_terms(world.engine, other_chunk, "repo_a", 20.0)
    _plant_terms(world.engine, other_chunk, "repo_b", 21.0)
    _close(world.engine, other_chunk)
    two = world.submit("ch_1", generation=2, resources=both)
    future = world.submit("ch_1", generation=3, resources=both)
    _plant_terms(world.engine, future, "repo_a", 30.0)
    _plant_terms(world.engine, future, "repo_b", 31.0)
    assert two is not None

    _turn(world, two, (LandingTerms("repo_a", "merge-on-checks", None, 99.0),))

    assert flows.terms_of(two.submission_id) == {"repo_a": newer_a, "repo_b": older_b}


def test_a_repository_with_no_earlier_terms_takes_its_candidate_and_one_with_neither_takes_none(
    world: _World, flows: FlowStore
) -> None:
    zero = world.submit("ch_1", resources=[_MAIN])
    earlier = _plant_terms(world.engine, zero, "repo_a", 1.0)
    _close(world.engine, zero)
    one = world.submit("ch_1", generation=1, resources=[_MAIN, _OTHER, _THIRD])
    assert one is not None
    candidate = LandingTerms("repo_b", "merge-on-checks", None, 5.0)

    _turn(world, one, (candidate,))

    assert flows.terms_of(one.submission_id) == {"repo_a": earlier, "repo_b": candidate}


def _terms_rows_read_by_turn(world: _World, depth: int) -> int:
    chunk_id = f"ch_depth{depth}"
    for generation in range(depth):
        past = world.submit(chunk_id, generation=generation, resources=[_MAIN, _OTHER, _THIRD])
        for repository_id in ("repo_a", "repo_b", "repo_c", "repo_x", "repo_y"):
            _plant_terms(world.engine, past, repository_id, float(generation))
        _close(world.engine, past)
    current = world.submit(chunk_id, generation=depth, resources=[_MAIN, _OTHER])
    assert current is not None
    # Only the statements over the terms table: take_turn's other reads grow with the tenant's other rows.
    with capture_statements(world.engine) as statements:
        turn = world.turn(current)
    world.flows.end_turn_interrupted(turn.turn_id, withdrawal=None, event=_INTERRUPTED, at=_AT)
    total = 0
    with world.engine.connect() as conn:
        for statement, parameters in statements:
            if statement.lstrip().upper().startswith("SELECT") and "flow_submission_terms" in statement:
                total += conn.exec_driver_sql(f"SELECT count(*) FROM ({statement})", parameters).scalar_one()
    _close(world.engine, current)
    return total


def test_the_rows_a_turn_reads_for_earlier_terms_do_not_grow_with_the_visits_history(world: _World) -> None:
    shallow = _terms_rows_read_by_turn(world, 2)
    deep = _terms_rows_read_by_turn(world, 6)

    # The held repositories each read one newest earlier row, whatever the history behind it.
    assert 0 < shallow == deep


def test_new_epoch_takes_new_allow_no_ci_terms(world: _World, flows: FlowStore) -> None:
    first = world.submit("ch_1")
    assert first is not None
    turn = _turn(flows, first.submission_id, (_TERMS_A,))
    flows.end_turn_interrupted(turn, withdrawal="superseded", event=_INTERRUPTED, at=_AT)
    next_visit = world.submit("ch_1", epoch=2)
    assert next_visit is not None
    changed = LandingTerms("repo_a", "merge-on-checks", None, 1.0, False)
    _turn(flows, next_visit.submission_id, (changed,))
    assert flows.terms_of(next_visit.submission_id) == {"repo_a": changed}


def test_requeue_in_same_epoch_retains_allow_no_ci_terms(world: _World, flows: FlowStore) -> None:
    first = world.submit("ch_1")
    assert first is not None
    original = LandingTerms("repo_a", "merge-on-checks", None, 1.0, True)
    turn = _turn(flows, first.submission_id, (original,))
    flows.end_turn_interrupted(turn, withdrawal="superseded", event=_INTERRUPTED, at=_AT)
    requeued = world.submit("ch_1", generation=1)
    assert requeued is not None
    changed = LandingTerms("repo_a", "merge-on-checks", None, 1.0, False)
    _turn(flows, requeued.submission_id, (changed,))
    assert flows.terms_of(requeued.submission_id) == {"repo_a": original}
