"""``FlowStore`` (component tier). A submission takes the tenant's newest arrival plus one and is written once per
generation of a visit; a closure is written once, with its ``withdrawn`` facts, and never while a turn is open; the
open walk from a floor returns only open submissions at or above it; and ending a turn ``interrupted`` writes its
``turn-ended`` facts and, when asked, the closure, once. A turn gives its submission landing terms for each repository
it holds and has none for — an earlier generation's, else the candidate — so a retried turn and a resubmission keep
what the visit's first turn took."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, Table, func, select

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.delivery.flows import FlowResource, LandingTerms, TurnEvent
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.flow_store import FlowStore
from tests.support import build_hub, hub_scoped, hub_store_connections, stamp

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
_MAIN = FlowResource("repo_a", "main")
_OTHER = FlowResource("repo_b", "master")
_INTERRUPTED = TurnEvent(kind="turn-interrupted", chunk_id="ch_1", node_name="deliver", message="ended", detail={})


@dataclass(frozen=True)
class _World:
    engine: Engine
    flows: FlowStore


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


def test_each_submission_arrives_at_the_tenants_newest_plus_one(flows: FlowStore) -> None:
    first = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    second = flows.submit("ch_2", node_id="nd_deliver", epoch=1, generation=0, resources=[_OTHER], at=_AT)
    third = flows.submit("ch_3", node_id="nd_deliver", epoch=1, generation=0, resources=[], at=_AT)

    assert first is not None and second is not None and third is not None
    assert (first.arrival, second.arrival, third.arrival) == (1, 2, 3)
    assert third.flows == ()


def test_a_submission_makes_its_flow_once_and_writes_a_submitted_fact_per_flow(world: _World, flows: FlowStore) -> None:
    first = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN, _OTHER], at=_AT)
    second = flows.submit("ch_2", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)

    assert first is not None and second is not None
    assert {f.resource for f in first.flows} == {"repo:repo_a/main", "repo:repo_b/master"}
    assert second.flows == tuple(f for f in first.flows if f.resource == "repo:repo_a/main")
    assert _count(world.engine, s.flows) == 2
    assert [(f.kind, f.flow_id) for f in flows.facts_of(second.submission_id)] == [
        ("submitted", second.flows[0].flow_id)
    ]


def test_a_second_submit_of_the_same_visit_generation_writes_nothing(world: _World, flows: FlowStore) -> None:
    first = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)

    again = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)

    assert first is not None and again is None
    assert _count(world.engine, s.flow_submissions) == 1
    assert _count(world.engine, s.flow_facts) == 1
    resubmitted = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=1, resources=[_MAIN], at=_AT)
    assert resubmitted is not None and resubmitted.arrival == 2


def test_a_submission_is_closed_once_with_one_withdrawn_fact_per_flow(world: _World, flows: FlowStore) -> None:
    submission = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN, _OTHER], at=_AT)
    assert submission is not None

    assert flows.close(submission.submission_id, outcome="superseded", at=_AT) is not None
    assert flows.close(submission.submission_id, outcome="stopped", at=_AT + timedelta(seconds=1)) is None

    withdrawn = [f for f in flows.facts_of(submission.submission_id) if f.kind == "withdrawn"]
    assert sorted((f.flow_id, f.outcome) for f in withdrawn) == sorted(
        (flow.flow_id, "superseded") for flow in submission.flows
    )
    assert _count(world.engine, s.flow_submission_closures) == 1
    visit = flows.submission_for_visit("ch_1", node_id="nd_deliver", epoch=1)
    assert visit is not None and visit.closed == "superseded"


def test_a_submission_with_an_open_turn_is_not_closed(world: _World, flows: FlowStore) -> None:
    submission = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    assert submission is not None
    _plant_turn(world.engine, submission.submission_id)

    assert flows.close(submission.submission_id, outcome="superseded", at=_AT) is None
    assert _count(world.engine, s.flow_submission_closures) == 0


def test_the_floor_walk_returns_only_open_submissions_at_or_above_the_floor(flows: FlowStore) -> None:
    submitted = [
        flows.submit(f"ch_{n}", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
        for n in range(1, 5)
    ]
    assert all(submitted)
    ids = [sub.submission_id for sub in submitted if sub is not None]
    flows.close(ids[2], outcome="completed", at=_AT)

    walked = flows.open_submissions_from(2)

    assert [sub.submission_id for sub in walked] == [ids[1], ids[3]]
    assert [sub.arrival for sub in walked] == [2, 4]
    assert all(sub.closed is None and len(sub.flows) == 1 for sub in walked)


def test_the_per_visit_lookup_answers_the_newest_generation(flows: FlowStore) -> None:
    assert flows.submission_for_visit("ch_1", node_id="nd_deliver", epoch=1) is None
    flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    newer = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=1, resources=[_MAIN], at=_AT)

    visit = flows.submission_for_visit("ch_1", node_id="nd_deliver", epoch=1)

    assert visit == newer
    assert flows.submission_for_visit("ch_1", node_id="nd_deliver", epoch=2) is None


def test_ending_a_turn_interrupted_writes_its_facts_and_closure_once(world: _World, flows: FlowStore) -> None:
    submission = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
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
    submission = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    assert submission is not None
    _plant_turn(world.engine, submission.submission_id)

    assert flows.end_turn_interrupted("htn_1", withdrawal=None, event=_INTERRUPTED, at=_AT) is not None

    assert [sub.submission_id for sub in flows.open_submissions_from(0)] == [submission.submission_id]


_TERMS_A = LandingTerms("repo_a", "merge-on-checks", None, 1.0)
_TERMS_B = LandingTerms("repo_b", "merge-on-checks", None, 2.0)
_THIRD = FlowResource("repo_c", "main")


def _turn(flows: FlowStore, submission_id: str, terms: tuple[LandingTerms, ...], *, at: datetime = _AT) -> str:
    turn = flows.take_turn(submission_id, passed_over=frozenset(), terms=terms, at=at)
    assert turn is not None
    return turn.turn_id


def test_a_first_turn_takes_terms_for_every_repository_it_holds(flows: FlowStore) -> None:
    submission = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN, _OTHER], at=_AT)
    assert submission is not None
    assert flows.terms_of(submission.submission_id) == {}

    _turn(flows, submission.submission_id, (_TERMS_A, _TERMS_B))

    assert flows.terms_of(submission.submission_id) == {"repo_a": _TERMS_A, "repo_b": _TERMS_B}


def test_a_retried_turn_keeps_the_terms_its_first_attempt_took(world: _World, flows: FlowStore) -> None:
    submission = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    assert submission is not None
    first = _turn(flows, submission.submission_id, (_TERMS_A,))
    flows.end_turn_interrupted(first, withdrawal=None, event=_INTERRUPTED, at=_AT)

    _turn(flows, submission.submission_id, (LandingTerms("repo_a", "merge-on-checks", None, 9.0),))

    assert flows.terms_of(submission.submission_id) == {"repo_a": _TERMS_A}
    assert _count(world.engine, s.flow_submission_terms) == 1


def test_a_resubmission_copies_the_visits_terms_and_takes_fresh_ones_only_for_a_new_repository(
    flows: FlowStore,
) -> None:
    first = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN, _OTHER], at=_AT)
    assert first is not None
    turn = _turn(flows, first.submission_id, (_TERMS_A, _TERMS_B))
    flows.end_turn_interrupted(turn, withdrawal="superseded", event=_INTERRUPTED, at=_AT)
    second = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=1, resources=[_MAIN, _THIRD], at=_AT)
    assert second is not None
    fresh_c = LandingTerms("repo_c", "merge-on-checks", None, None)

    _turn(flows, second.submission_id, (LandingTerms("repo_a", "merge-on-checks", None, 5.0), fresh_c))

    assert flows.terms_of(second.submission_id) == {"repo_a": _TERMS_A, "repo_c": fresh_c}


def test_the_first_turn_of_a_visit_is_kept_across_its_resubmissions(flows: FlowStore) -> None:
    assert flows.first_turn_at("ch_1", node_id="nd_deliver", epoch=1) is None
    first = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    assert first is not None
    turn = _turn(flows, first.submission_id, (_TERMS_A,))
    flows.end_turn_interrupted(turn, withdrawal="superseded", event=_INTERRUPTED, at=_AT)
    second = flows.submit("ch_1", node_id="nd_deliver", epoch=1, generation=1, resources=[_MAIN], at=_AT)
    assert second is not None

    _turn(flows, second.submission_id, (_TERMS_A,), at=_AT + timedelta(hours=1))

    assert flows.first_turn_at("ch_1", node_id="nd_deliver", epoch=1) == _AT
    assert flows.first_turn_at("ch_1", node_id="nd_deliver", epoch=2) is None
