"""The operational analytics domain folds (unit tier):
:func:`resolve_attempt_failures`'s base cases, :func:`fold_step_durations`'s
chained-interval attribution, and :func:`steps_in_window`'s post-fold time filter,
pinned as pure functions over hand-built facts with no store standing up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.analytics.operational import (
    JudgedChoiceCount,
    LeaseEpoch,
    MigrationMovement,
    MissingGraphFact,
    OutcomeStats,
    SpendStats,
    StepDuration,
    TransitionMovement,
    fold_spend_by_name,
    fold_step_durations,
    group_judged_choices,
    resolve_attempt_failures,
    steps_in_window,
    summarize_durations,
    summarize_outcomes,
)
from blizzard.hub.domain.analytics.queries import KeyedCount, fold_counts_by_name
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.work import UsageTotal

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 8, 12, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def test_an_in_flight_epoch_is_excluded_with_no_end_of_attempt_evidence() -> None:
    """A lone unresolved epoch that is still the chunk's newest lease is running,
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
    """The base case with zero prior movement — a strictly newer lease is the positive
    end-of-attempt evidence epoch 1 needs."""
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
    """Mirrors ``ChunkFacts._latest_movement_is_migration`` — a migration recorded at
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


def test_a_graph_id_missing_from_the_preload_raises_a_named_error_not_a_keyerror() -> None:
    """The adapter preloads exactly the graph ids the fold's own indexing needs —
    a miss (e.g. a `chunk_migrations` row whose graph id no longer has a `graphs` row,
    unenforced by any FK) must surface as a diagnosable error, not a bare KeyError."""
    with pytest.raises(MissingGraphFact):
        resolve_attempt_failures(
            lease_epochs=[LeaseEpoch(chunk_id="ch_1", epoch=1, minted_at=_at(0))],
            transitions=[],
            migrations=[],
            bounced=[],
            chunk_graph={},  # missing: no entry for "ch_1"
            chunk_max_lease_epoch={"ch_1": 2},
            graph_entry_node={},
            graph_id_filter=None,
        )


def test_a_terminal_transition_is_never_read_as_a_prior_movements_node() -> None:
    """``transitions.to_node_id`` can be ``RESERVED_TERMINAL`` ("done"), not a node
    id — a later unresolved epoch resolving through it must fall through to the
    no-movement case instead."""
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
                from_node_id="nd_review",
                to_node_id=RESERVED_TERMINAL,
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
    assert failures == {"nd_entry": 1}  # falls through to the entry node, never "done"


def test_a_same_instant_migration_tie_breaks_deterministically_on_migration_id() -> None:
    """The no-movement fallback's tie-break must be a total order — two later
    migrations at the identical ``(epoch, recorded_at)`` break on their own
    (schema-unique) ``migration_id``."""
    later_a = MigrationMovement(
        chunk_id="ch_1",
        epoch=2,
        migration_id="mg_A",
        landed_node_id="nd_a_entry",
        from_graph_id="gr_a",
        to_graph_id="gr_c",
        recorded_at=_at(15),
    )
    later_b = MigrationMovement(
        chunk_id="ch_1",
        epoch=2,
        migration_id="mg_B",
        landed_node_id="nd_b_entry",
        from_graph_id="gr_b",
        to_graph_id="gr_c",
        recorded_at=_at(15),  # same instant as later_a
    )
    common = {
        "lease_epochs": [
            LeaseEpoch(chunk_id="ch_1", epoch=1, minted_at=_at(0)),
            LeaseEpoch(chunk_id="ch_1", epoch=2, minted_at=_at(20)),
        ],
        "transitions": [],
        "bounced": [],
        "chunk_graph": {"ch_1": "gr_c"},
        "chunk_max_lease_epoch": {"ch_1": 2},
        "graph_entry_node": {"gr_a": "nd_a_entry", "gr_b": "nd_b_entry", "gr_c": "nd_c_entry"},
        "graph_id_filter": None,
    }
    # mg_A sorts before mg_B, so the tie always resolves to A's from_graph_id — regardless
    # of which order the two migrations are passed in.
    assert resolve_attempt_failures(migrations=[later_a, later_b], **common) == {"nd_a_entry": 1}
    assert resolve_attempt_failures(migrations=[later_b, later_a], **common) == {"nd_a_entry": 1}


def test_a_bounced_epoch_is_excluded_outright() -> None:
    """The bounced epoch (round 4) must be superseded by a strictly newer lease, or
    the unrelated in-flight guard excludes it first and masks the ``bounced_set`` check."""
    failures = resolve_attempt_failures(
        lease_epochs=[
            LeaseEpoch(chunk_id="ch_1", epoch=1, minted_at=_at(0)),
            LeaseEpoch(chunk_id="ch_1", epoch=2, minted_at=_at(10)),
        ],
        transitions=[],
        migrations=[],
        bounced=[("ch_1", 1)],
        chunk_graph={"ch_1": "gr_1"},
        chunk_max_lease_epoch={"ch_1": 2},
        graph_entry_node={"gr_1": "nd_entry"},
        graph_id_filter=None,
    )
    assert failures == {}


def test_fold_step_durations_chains_two_transitions_sharing_one_epoch() -> None:
    """The first transition in an epoch measures from the lease mint; the second
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
    """The fold sorts explicitly rather than trusting the order rows arrived in."""
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


def test_steps_in_window_re_checks_graph_id_too() -> None:
    """The store's fetch (review round 4) narrows by ``(chunk_id, epoch)`` group,
    not by graph — this re-check is what actually enforces a ``graph_id`` filter
    against a hand-built input that violates the one-group-one-graph invariant."""
    rows = [
        StepDuration(from_node_id="nd_a", graph_id="gr_a", seconds=1.0, recorded_at=_at(1)),
        StepDuration(from_node_id="nd_b", graph_id="gr_b", seconds=2.0, recorded_at=_at(2)),
    ]

    assert steps_in_window(rows, since=None, until=None, graph_id="gr_a") == [rows[0]]
    assert steps_in_window(rows, since=None, until=None, graph_id=None) == rows


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


def test_group_judged_choices_groups_by_node_then_choice() -> None:
    """The judged-distribution grouping is a domain fold, not adapter-only logic."""
    rows = [
        JudgedChoiceCount(from_node_id="nd_build", choice_name="pass", occurrences=3),
        JudgedChoiceCount(from_node_id="nd_build", choice_name="fail", occurrences=1),
        JudgedChoiceCount(from_node_id="nd_review", choice_name="pass", occurrences=2),
    ]

    assert group_judged_choices(rows) == {
        "nd_build": {"pass": 3, "fail": 1},
        "nd_review": {"pass": 2},
    }


def test_summarize_outcomes_merges_both_halves_and_never_drops_a_node_with_only_one() -> None:
    """The merge rule (a node with neither a judged choice nor an attempt
    failure never appears) as a domain fold, node id ascending."""
    judged = {"nd_b": {"pass": 1}}
    failures = {"nd_a": 2}

    assert summarize_outcomes(judged, failures) == [
        OutcomeStats(node_id="nd_a", choice_counts={}, attempt_failures=2),
        OutcomeStats(node_id="nd_b", choice_counts={"pass": 1}, attempt_failures=0),
    ]


# --- the by-name roll-up folds -------------------------------------------------------


def _total(
    tokens: int,
    *,
    cost: float = 0.0,
    estimated: float | None = None,
    partial: bool = False,
    output: int = 0,
    cache_read: int = 0,
    cache_create: int = 0,
    billed: float | None = None,
    billed_partial: bool = False,
) -> UsageTotal:
    return UsageTotal(
        input_tokens=tokens,
        output_tokens=output,
        cache_read_tokens=cache_read,
        cache_create_tokens=cache_create,
        cost_usd=cost,
        cost_partial=partial,
        estimated_cost_usd=estimated,
        billed_partial=billed_partial,
        billed_cost_usd=billed,
    )


def test_spend_folds_two_mints_of_one_node_and_keeps_same_named_nodes_of_other_graphs_apart() -> None:
    rows = [
        SpendStats("nd_1", _total(10, cost=0.1), graph_name="adv", node_name="build"),
        SpendStats("nd_2", _total(5, cost=0.2, estimated=0.5, partial=True), graph_name="adv", node_name="build"),
        SpendStats("nd_3", _total(7), graph_name="bas", node_name="build"),
        SpendStats("nd_gone", _total(1)),
    ]

    folded = {r.key: r for r in fold_spend_by_name(rows)}

    assert sorted(folded) == ["adv/build", "bas/build", "nd_gone"]
    assert folded["adv/build"].total.input_tokens == 15
    assert folded["adv/build"].total.cost_usd == pytest.approx(0.3)
    assert folded["adv/build"].total.cost_partial is True
    assert folded["adv/build"].total.estimated_cost_usd == 0.5
    assert folded["bas/build"].total.estimated_cost_usd is None
    assert folded["nd_gone"].graph_name is None


def test_spend_fold_sums_every_token_kind_and_folds_the_billed_figures() -> None:
    """Each summed field carries a distinct non-zero value per row, so dropping any one sum,
    or folding billed over only some rows, changes an asserted number."""
    rows = [
        SpendStats(
            "nd_1",
            _total(10, output=20, cache_read=30, cache_create=40, cost=0.5, billed=0.5),
            graph_name="adv",
            node_name="build",
        ),
        SpendStats(
            "nd_2",
            _total(1, output=2, cache_read=3, cache_create=4, cost=0.25, billed=0.25, billed_partial=True),
            graph_name="adv",
            node_name="build",
        ),
        SpendStats(
            "nd_3",
            _total(100, output=200, cache_read=300, cache_create=400, estimated=0.75, billed_partial=True),
            graph_name="adv",
            node_name="build",
        ),
    ]

    [row] = fold_spend_by_name(rows)

    total = row.total
    assert (total.input_tokens, total.output_tokens) == (111, 222)
    assert (total.cache_read_tokens, total.cache_create_tokens) == (333, 444)
    assert total.cost_usd == pytest.approx(0.75)
    assert total.estimated_cost_usd == pytest.approx(0.75)
    assert total.billed_cost_usd == pytest.approx(0.75)
    assert total.billed_partial is True
    assert total.cost_partial is False


def test_spend_fold_leaves_billed_unset_and_not_partial_when_no_row_carries_it() -> None:
    rows = [
        SpendStats("nd_1", _total(1, estimated=0.5, billed_partial=True), graph_name="adv", node_name="build"),
        SpendStats("nd_2", _total(2, estimated=0.5, billed_partial=True), graph_name="adv", node_name="build"),
    ]

    [row] = fold_spend_by_name(rows)

    assert row.total.billed_cost_usd is None
    assert row.total.billed_partial is True


def test_spend_fold_reports_billed_complete_only_when_every_row_is_billed() -> None:
    rows = [
        SpendStats("nd_1", _total(1, billed=0.5), graph_name="adv", node_name="build"),
        SpendStats("nd_2", _total(2, billed=0.5), graph_name="adv", node_name="build"),
    ]

    [row] = fold_spend_by_name(rows)

    assert row.total.billed_partial is False
    assert row.total.billed_cost_usd == pytest.approx(1.0)


def test_spend_folds_graph_rows_by_graph_name() -> None:
    rows = [SpendStats("gr_1", _total(1), graph_name="adv"), SpendStats("gr_2", _total(2), graph_name="adv")]

    [row] = fold_spend_by_name(rows)

    assert (row.key, row.total.input_tokens, row.node_name) == ("adv", 3, None)


def test_counts_fold_sums_and_orders_by_count_then_key() -> None:
    rows = [
        KeyedCount("nd_1", 2, "adv", "build"),
        KeyedCount("nd_2", 3, "adv", "build"),
        KeyedCount("nd_3", 5, "bas", "build"),
        KeyedCount("nd_4", 5),
        KeyedCount("nd_5", 7, "cas", "review"),
        KeyedCount("nd_6", 1, "cas", "build"),
    ]

    folded = fold_counts_by_name(rows)

    assert [(r.key, r.count, r.graph_name, r.node_name) for r in folded] == [
        ("cas/review", 7, "cas", "review"),
        ("adv/build", 5, "adv", "build"),
        ("bas/build", 5, "bas", "build"),
        ("nd_4", 5, None, None),
        ("cas/build", 1, "cas", "build"),
    ]
