"""The operational analytics domain folds (blizzard#256 — unit tier):
:func:`resolve_attempt_failures`'s D5 base cases, :func:`fold_step_durations`'s
chained-interval attribution, and :func:`steps_in_window`'s post-fold time filter,
pinned as pure functions over hand-built facts with no store standing up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.analytics.operational import (
    LeaseEpoch,
    MigrationMovement,
    StepDuration,
    TransitionMovement,
    fold_step_durations,
    resolve_attempt_failures,
    steps_in_window,
    summarize_durations,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 8, 12, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def test_an_in_flight_epoch_is_excluded_with_no_end_of_attempt_evidence() -> None:
    """F1: a lone unresolved epoch that is still the chunk's newest lease is running,
    not failed — no positive evidence it has ended."""
    failures = resolve_attempt_failures(
        lease_epochs=[LeaseEpoch(chunk_id="ch_1", epoch=1, minted_at=_at(0))],
        transitions=[],
        migrations=[],
        bounced=[],
        chunk_graph={"ch_1": "gr_1"},
        chunk_max_lease_epoch={"ch_1": 1},
        graph_entry_node={"gr_1": "nd_entry"},
        graph_id_filter=None,
    )
    assert failures == {}


def test_a_superseded_epoch_with_no_movement_counts_via_the_entry_node() -> None:
    """D5's base case with zero prior movement — a strictly newer lease is the positive
    end-of-attempt evidence epoch 1 needs (F1)."""
    failures = resolve_attempt_failures(
        lease_epochs=[
            LeaseEpoch(chunk_id="ch_1", epoch=1, minted_at=_at(0)),
            LeaseEpoch(chunk_id="ch_1", epoch=2, minted_at=_at(10)),
        ],
        transitions=[],
        migrations=[],
        bounced=[],
        chunk_graph={"ch_1": "gr_1"},
        chunk_max_lease_epoch={"ch_1": 2},
        graph_entry_node={"gr_1": "nd_entry"},
        graph_id_filter=None,
    )
    assert failures == {"nd_entry": 1}  # epoch 1 only — epoch 2 is still in flight


def test_a_superseded_epoch_resolves_via_the_prior_transitions_to_node() -> None:
    """Epoch 1 resolves (a transition of its own, excluded from the count outright);
    epoch 2 crashed with no transition of its own but resolves via epoch 1's; epoch 3
    is the positive evidence epoch 2 is over, and is itself excluded as still in flight."""
    failures = resolve_attempt_failures(
        lease_epochs=[
            LeaseEpoch(chunk_id="ch_1", epoch=1, minted_at=_at(0)),
            LeaseEpoch(chunk_id="ch_1", epoch=2, minted_at=_at(10)),
            LeaseEpoch(chunk_id="ch_1", epoch=3, minted_at=_at(20)),
        ],
        transitions=[
            TransitionMovement(
                chunk_id="ch_1",
                epoch=1,
                transition_id="tr_1",
                from_node_id="nd_entry",
                to_node_id="nd_review",
                graph_id="gr_1",
                recorded_at=_at(5),
            )
        ],
        migrations=[],
        bounced=[],
        chunk_graph={"ch_1": "gr_1"},
        chunk_max_lease_epoch={"ch_1": 3},
        graph_entry_node={"gr_1": "nd_entry"},
        graph_id_filter=None,
    )
    assert failures == {"nd_review": 1}


def test_a_same_instant_tie_between_a_transition_and_a_migration_goes_to_the_migration() -> None:
    """F4: mirrors ``ChunkFacts._latest_movement_is_migration`` — a migration recorded at
    the same instant and epoch as a transition is the later movement. Epoch 2 is the
    failure under test; epoch 3 is the positive evidence it is over."""
    failures = resolve_attempt_failures(
        lease_epochs=[
            LeaseEpoch(chunk_id="ch_1", epoch=2, minted_at=_at(0)),
            LeaseEpoch(chunk_id="ch_1", epoch=3, minted_at=_at(20)),
        ],
        transitions=[
            TransitionMovement(
                chunk_id="ch_1",
                epoch=1,
                transition_id="tr_1",
                from_node_id="nd_entry",
                to_node_id="nd_stale",
                graph_id="gr_1",
                recorded_at=_at(5),
            )
        ],
        migrations=[
            MigrationMovement(
                chunk_id="ch_1",
                epoch=1,
                migration_id="mg_1",
                landed_node_id="nd_landed",
                from_graph_id="gr_1",
                to_graph_id="gr_2",
                recorded_at=_at(5),  # same instant, same epoch as the transition above
            )
        ],
        bounced=[],
        chunk_graph={"ch_1": "gr_2"},
        chunk_max_lease_epoch={"ch_1": 3},
        graph_entry_node={"gr_1": "nd_entry", "gr_2": "nd_2_entry"},
        graph_id_filter=None,
    )
    assert failures == {"nd_landed": 1}


def test_a_no_movement_failure_resolves_via_the_epoch_s_own_graph_not_the_current_pin() -> None:
    """A later migration re-pins the chunk — the no-movement fallback must resolve via
    the graph the chunk ran in AT the failure epoch (that migration's own
    ``from_graph_id``), not the chunk's current, already-migrated-to pin."""
    failures = resolve_attempt_failures(
        lease_epochs=[
            LeaseEpoch(chunk_id="ch_1", epoch=1, minted_at=_at(0)),
            LeaseEpoch(chunk_id="ch_1", epoch=2, minted_at=_at(20)),
        ],
        transitions=[],
        migrations=[
            MigrationMovement(
                chunk_id="ch_1",
                epoch=2,
                migration_id="mg_1",
                landed_node_id="nd_b_entry",
                from_graph_id="gr_a",
                to_graph_id="gr_b",
                recorded_at=_at(15),
            )
        ],
        bounced=[],
        chunk_graph={"ch_1": "gr_b"},  # the chunk's CURRENT pin, post-migration
        chunk_max_lease_epoch={"ch_1": 2},
        graph_entry_node={"gr_a": "nd_a_entry", "gr_b": "nd_b_entry"},
        graph_id_filter=None,
    )
    # Epoch 1 ran on graph A and must resolve via A's entry, not B's (the current pin).
    assert failures == {"nd_a_entry": 1}


def test_a_bounced_epoch_is_excluded_outright() -> None:
    failures = resolve_attempt_failures(
        lease_epochs=[LeaseEpoch(chunk_id="ch_1", epoch=1, minted_at=_at(0))],
        transitions=[],
        migrations=[],
        bounced=[("ch_1", 1)],
        chunk_graph={"ch_1": "gr_1"},
        chunk_max_lease_epoch={"ch_1": 1},
        graph_entry_node={"gr_1": "nd_entry"},
        graph_id_filter=None,
    )
    assert failures == {}


def test_fold_step_durations_chains_two_transitions_sharing_one_epoch() -> None:
    """F3: the first transition in an epoch measures from the lease mint; the second
    measures from the first, not from the mint again."""
    rows = fold_step_durations(
        transitions=[
            TransitionMovement(
                chunk_id="ch_1",
                epoch=1,
                transition_id="tr_1",
                from_node_id="nd_build",
                to_node_id="nd_gate",
                graph_id="gr_1",
                recorded_at=_at(10),
            ),
            TransitionMovement(
                chunk_id="ch_1",
                epoch=1,
                transition_id="tr_2",
                from_node_id="nd_gate",
                to_node_id="nd_done",
                graph_id="gr_1",
                recorded_at=_at(110),
            ),
        ],
        lease_min_by_epoch={("ch_1", 1): _at(0)},
    )
    by_node = {r.from_node_id: r.seconds for r in rows}
    assert by_node == {"nd_build": 10.0, "nd_gate": 100.0}


def test_fold_step_durations_orders_by_recorded_at_not_row_arrival() -> None:
    """F4: the fold sorts explicitly rather than trusting the order rows arrived in."""
    rows = fold_step_durations(
        transitions=[
            TransitionMovement(
                chunk_id="ch_1",
                epoch=1,
                transition_id="tr_2",
                from_node_id="nd_gate",
                to_node_id="nd_done",
                graph_id="gr_1",
                recorded_at=_at(110),  # later, but listed first
            ),
            TransitionMovement(
                chunk_id="ch_1",
                epoch=1,
                transition_id="tr_1",
                from_node_id="nd_build",
                to_node_id="nd_gate",
                graph_id="gr_1",
                recorded_at=_at(10),
            ),
        ],
        lease_min_by_epoch={("ch_1", 1): _at(0)},
    )
    by_node = {r.from_node_id: r.seconds for r in rows}
    assert by_node == {"nd_build": 10.0, "nd_gate": 100.0}


def test_fold_step_durations_excludes_a_transition_with_no_matching_lease() -> None:
    rows = fold_step_durations(
        transitions=[
            TransitionMovement(
                chunk_id="ch_1",
                epoch=1,
                transition_id="tr_1",
                from_node_id="nd_build",
                to_node_id="nd_done",
                graph_id="gr_1",
                recorded_at=_at(10),
            )
        ],
        lease_min_by_epoch={},
    )
    assert rows == []


def test_a_node_less_row_is_skipped_from_the_node_rollup_but_counted_in_the_graph_one() -> None:
    rows = [
        StepDuration(from_node_id=None, graph_id="gr_1", seconds=10.0, recorded_at=_at(10)),
        StepDuration(from_node_id="nd_build", graph_id="gr_1", seconds=30.0, recorded_at=_at(30)),
    ]

    by_node = summarize_durations(rows, key="node")
    assert [(r.key, r.completed_steps, r.total_seconds) for r in by_node] == [("nd_build", 1, 30.0)]

    by_graph = summarize_durations(rows, key="graph")
    assert [(r.key, r.completed_steps, r.total_seconds, r.avg_seconds) for r in by_graph] == [("gr_1", 2, 40.0, 20.0)]


def test_steps_in_window_keeps_only_rows_whose_own_transition_is_inside_it() -> None:
    rows = [
        StepDuration(from_node_id="nd_build", graph_id="gr_1", seconds=10.0, recorded_at=_at(10)),
        StepDuration(from_node_id="nd_gate", graph_id="gr_1", seconds=100.0, recorded_at=_at(110)),
    ]

    assert steps_in_window(rows, since=_at(11), until=None) == [rows[1]]
    assert steps_in_window(rows, since=None, until=_at(110)) == [rows[0]]
    assert steps_in_window(rows, since=None, until=None) == rows


def test_fold_step_durations_then_steps_in_window_does_not_break_a_chained_epoch() -> None:
    """A window edge between two transitions sharing one epoch must not drop the
    earlier one from the fold's own input — the survivor still measures from its true
    predecessor; only its own output is dropped, and only after the fold."""
    transitions = [
        TransitionMovement(
            chunk_id="ch_1",
            epoch=1,
            transition_id="tr_1",
            from_node_id="nd_build",
            to_node_id="nd_gate",
            graph_id="gr_1",
            recorded_at=_at(10),
        ),
        TransitionMovement(
            chunk_id="ch_1",
            epoch=1,
            transition_id="tr_2",
            from_node_id="nd_gate",
            to_node_id="nd_done",
            graph_id="gr_1",
            recorded_at=_at(110),
        ),
    ]
    all_rows = fold_step_durations(transitions, lease_min_by_epoch={("ch_1", 1): _at(0)})

    windowed = steps_in_window(all_rows, since=_at(11), until=None)

    assert [(r.from_node_id, r.seconds) for r in windowed] == [("nd_gate", 100.0)]
