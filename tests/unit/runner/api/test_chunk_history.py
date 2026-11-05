"""``GET /api/leases/{id}/history``'s pure projection, ``runner.api.history.rows``.

``rows`` over a fixture — a bounced attempt that produced no artifact still
becomes a row, a migration becomes its own row, and everything merges oldest-first."""

from __future__ import annotations

from blizzard.foundation.migration_source import MigrationSource
from blizzard.runner.api.history import rows
from blizzard.wire.chunk import BounceView, MigrationView, TransitionView
from blizzard.wire.history import ChunkHistoryView
from tests.support.chunk_history_api import DETAIL


def test_history_rows_merges_all_three_kinds_oldest_first() -> None:
    detail = ChunkHistoryView.model_validate(DETAIL)
    views = rows(detail)
    assert [r.recorded_at for r in views] == [
        "2026-07-21T09:00:00+00:00",
        "2026-07-21T10:00:00+00:00",
        "2026-07-21T11:00:00+00:00",
        "2026-07-21T12:00:00+00:00",
    ]
    assert [r.kind for r in views] == ["migration", "transition", "bounce", "transition"]


def test_a_bounced_attempt_with_no_artifact_is_still_a_row() -> None:
    """#237 AC3: a bounce that produced no artifact anywhere in the envelope still
    appears on the timeline — it carries its own kick-back cause."""
    detail = ChunkHistoryView(
        history=[],
        migrations=[],
        bounces=[BounceView(cause="conflict", envelope="{}", recorded_at="2026-07-21T11:00:00+00:00")],
    )
    views = rows(detail)
    assert len(views) == 1
    assert views[0].kind == "bounce"
    assert views[0].cause == "conflict"
    assert views[0].from_node is None and views[0].to_node is None


def test_a_migration_becomes_its_own_row_with_a_graph_hop_label() -> None:
    detail = ChunkHistoryView(
        history=[],
        bounces=[],
        migrations=[
            MigrationView(
                from_node_id="nd_triage",
                from_node_name="triage",
                from_graph_id="gr_0",
                from_graph_name="triage-graph",
                to_graph_id="gr_1",
                to_graph_name="adv-dwf",
                landed_node_id="nd_build",
                landed_node_name="build",
                choice_name="route",
                source=MigrationSource.AUTHORED_EDGE,
                epoch=1,
                recorded_at="2026-07-21T09:00:00+00:00",
            )
        ],
    )
    views = rows(detail)
    assert len(views) == 1
    assert views[0].kind == "migration"
    assert views[0].from_node == "triage-graph/triage"
    assert views[0].to_node == "adv-dwf/build"
    assert views[0].detail == "authored-edge"
    assert views[0].epoch is None


def test_a_transition_row_carries_epoch_and_choice() -> None:
    detail = ChunkHistoryView(
        migrations=[],
        bounces=[],
        history=[
            TransitionView(
                from_node_id="nd_review",
                from_node_name="review",
                to_node_id="nd_build",
                to_node_name="build",
                choice_name="fail",
                epoch=2,
                recorded_at="2026-07-21T12:00:00+00:00",
                graph_id="gr_1",
                graph_name="adv-dwf",
            )
        ],
    )
    views = rows(detail)
    assert len(views) == 1
    row = views[0]
    assert row.kind == "transition"
    assert row.from_node == "review" and row.to_node == "build"
    assert row.choice == "fail"
    assert row.epoch == 2
    assert row.graph_name == "adv-dwf"
