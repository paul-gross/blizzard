"""Which unclosed decisions belong to a node's current visit, pinned by value — no repository, no clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.model import ChunkFacts, LeaseFact, RestartFact, TransitionFact
from tests.support.completion_facts import AT_BUILD, T1, arrived_at, decision_fact

_T2 = T1 + timedelta(minutes=1)


def test_a_never_moved_chunks_unclosed_decisions_at_its_entry_node_are_its_visits() -> None:
    held = decision_fact(node_id="nd_build", epoch=1)
    facts = replace(AT_BUILD, decisions=[held])
    assert facts.visit_decisions("nd_build") == [held]
    assert facts.current_visit_decision() == held


def test_a_decision_stamped_after_the_arrival_belongs_to_the_visit_whoever_holds_it_next() -> None:
    # A runner gate opened by the attempt at epoch 3 after an arrival at epoch 1, read with a later lease at 5.
    held = decision_fact(epoch=3)
    facts = replace(arrived_at("nd_gate", epoch=1, decisions=[held]), leases=[LeaseFact(epoch=5, minted_at=_T2)])
    assert facts.visit_decisions("nd_gate") == [held]


def test_a_decision_below_the_arrival_epoch_belongs_to_an_earlier_visit() -> None:
    facts = arrived_at("nd_gate", epoch=4, decisions=[decision_fact(epoch=3)])
    assert facts.visit_decisions("nd_gate") == []
    assert facts.current_visit_decision() is None


def test_a_closed_decision_at_the_arrival_epoch_is_the_self_loop_exit_that_produced_the_arrival() -> None:
    closed = decision_fact(epoch=2, closed=True)
    facts = arrived_at("nd_gate", epoch=2, decisions=[closed])
    assert facts.visit_decisions("nd_gate") == []


def test_only_the_chunks_current_node_has_a_visit() -> None:
    facts = arrived_at("nd_review", epoch=1, decisions=[decision_fact(epoch=1)])
    assert facts.visit_decisions("nd_gate") == []
    assert facts.current_visit_decision() is None


def test_a_restart_back_onto_the_node_starts_a_new_visit() -> None:
    facts = replace(
        arrived_at("nd_gate", epoch=1, decisions=[decision_fact(epoch=1)]),
        restarts=[RestartFact(epoch=2, recorded_at=_T2, to_node_id="nd_gate", from_node_id="nd_gate", graph_id="gr_a")],
    )
    assert facts.visit_decisions("nd_gate") == []


def test_more_than_one_member_is_reported_as_all_of_them_and_read_as_the_newest() -> None:
    older, newer = decision_fact("dec_1", epoch=1), decision_fact("dec_2", epoch=2, submitted_at=_T2)
    facts: ChunkFacts = arrived_at("nd_gate", epoch=1, decisions=[older, newer])
    assert facts.visit_decisions("nd_gate") == [older, newer]
    assert facts.current_visit_decision() == newer


def test_membership_reads_the_canonical_movement_order_not_a_stamp_alone() -> None:
    # Two arrivals onto the node at one instant: the higher epoch is the current visit's.
    facts = replace(
        arrived_at("nd_gate", epoch=2, decisions=[decision_fact(epoch=2)]),
        transitions=[
            TransitionFact(to_node_id="nd_gate", to_node_executor=Executor.RUNNER, epoch=e, recorded_at=T1)
            for e in (1, 2)
        ],
    )
    assert [d.epoch for d in facts.visit_decisions("nd_gate")] == [2]
