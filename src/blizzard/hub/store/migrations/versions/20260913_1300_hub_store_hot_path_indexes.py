"""Hub store hot-path indexes (blizzard#517, blizzard#519): index every unindexed
predicate/order-by the synchronous read surface actually filters or sorts on, and
replace the three single-column ``chunk_id`` indexes the new ``(chunk_id, epoch)``
composites supersede.

Revision ID: 20260913_1300_hub_store_hot_path_indexes
Revises: 20260913_1200_close_intent_attempts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_1300_hub_store_hot_path_indexes"
down_revision: str | None = "20260913_1200_close_intent_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index name, table, columns) — blizzard#519's hot-path predicates, plus
# blizzard#517's `usage_facts.recorded_at`, plus one `(ts, pk)` index per table needing
# a newest-first bounded read since a timestamp (D6) — portable across sqlite and
# postgres alike (`bzh:sql-portable`).
_CREATES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_artifacts_chunk_id_node_id_epoch", "artifacts", ("chunk_id", "node_id", "epoch")),
    ("ix_graph_nodes_graph_id", "graph_nodes", ("graph_id",)),
    ("ix_graph_choices_node_id", "graph_choices", ("node_id",)),
    ("ix_graph_edges_from_node_id", "graph_edges", ("from_node_id",)),
    ("ix_graph_lifecycle_facts_graph_id", "graph_lifecycle_facts", ("graph_id",)),
    ("ix_graphs_name", "graphs", ("name",)),
    ("ix_transcript_segments_supersedes", "transcript_segments", ("supersedes",)),
    ("ix_transcript_segments_final_chunk_id", "transcript_segments", ("final", "chunk_id")),
    ("ix_chunk_work_refs_chunk_id", "chunk_work_refs", ("chunk_id",)),
    ("ix_chunk_work_refs_source_ref", "chunk_work_refs", ("source", "ref")),
    ("ix_usage_facts_node_id", "usage_facts", ("node_id",)),
    ("ix_usage_facts_recorded_at", "usage_facts", ("recorded_at",)),
    ("ix_work_item_proposals_chunk_id", "work_item_proposals", ("chunk_id",)),
    ("ix_close_intents_pending", "close_intents", ("retired_at",)),
    ("ix_transcript_events_extractor_version_id", "transcript_events", ("extractor_version", "id")),
    # The three (chunk_id, epoch) composites superseding a dropped single-column index.
    ("ix_transitions_chunk_id_epoch", "transitions", ("chunk_id", "epoch")),
    ("ix_lease_facts_chunk_id_epoch", "lease_facts", ("chunk_id", "epoch")),
    ("ix_chunk_bounces_chunk_id_epoch", "chunk_bounces", ("chunk_id", "epoch")),
    # activity_facts_since — string-pk sources: (ts, pk).
    ("ix_chunks_minted_at_chunk_id", "chunks", ("minted_at", "chunk_id")),
    ("ix_transitions_recorded_at_transition_id", "transitions", ("recorded_at", "transition_id")),
    ("ix_route_created_created_at_route_id", "route_created", ("created_at", "route_id")),
    ("ix_chunk_migrations_recorded_at_migration_id", "chunk_migrations", ("recorded_at", "migration_id")),
    ("ix_decisions_submitted_at_decision_id", "decisions", ("submitted_at", "decision_id")),
    (
        "ix_decision_resolutions_resolved_at_decision_id",
        "decision_resolutions",
        ("resolved_at", "decision_id"),
    ),
    ("ix_questions_asked_at_question_id", "questions", ("asked_at", "question_id")),
    ("ix_question_answers_answered_at_question_id", "question_answers", ("answered_at", "question_id")),
    # activity_facts_since — integer-id sources: (ts, id). sqlite would serve the
    # tie-break off a bare (ts) index by implicitly appending the rowid, but postgres
    # doesn't, so the id column rides explicitly for both (`bzh:sql-portable`).
    ("ix_chunk_promoted_promoted_at_id", "chunk_promoted", ("promoted_at", "id")),
    ("ix_chunk_grouped_grouped_at_id", "chunk_grouped", ("grouped_at", "id")),
    ("ix_chunk_restarts_recorded_at_id", "chunk_restarts", ("recorded_at", "id")),
    ("ix_escalations_recorded_at_id", "escalations", ("recorded_at", "id")),
    ("ix_requeues_requeued_at_id", "requeues", ("requeued_at", "id")),
    ("ix_route_released_released_at_id", "route_released", ("released_at", "id")),
    ("ix_chunk_pause_facts_set_at_id", "chunk_pause_facts", ("set_at", "id")),
    ("ix_chunk_stopped_stopped_at_id", "chunk_stopped", ("stopped_at", "id")),
    ("ix_chunk_completed_completed_at_id", "chunk_completed", ("completed_at", "id")),
    ("ix_chunk_deleted_deleted_at_id", "chunk_deleted", ("deleted_at", "id")),
)

# Superseded by a composite created above — dropped in the same migration (net-neutral
# on write cost, strictly better on reads). `ix_transitions_recorded_at` is superseded by
# `ix_transitions_recorded_at_transition_id`.
_DROPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_transitions_chunk_id", "transitions", ("chunk_id",)),
    ("ix_lease_facts_chunk_id", "lease_facts", ("chunk_id",)),
    ("ix_chunk_bounces_chunk_id", "chunk_bounces", ("chunk_id",)),
    ("ix_transitions_recorded_at", "transitions", ("recorded_at",)),
)


def _existing_index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for name, table, columns in _CREATES:
        if name not in _existing_index_names(bind, table):
            op.create_index(name, table, list(columns))
    for name, table, _columns in _DROPS:
        if name in _existing_index_names(bind, table):
            op.drop_index(name, table_name=table)


def downgrade() -> None:
    bind = op.get_bind()
    for name, table, columns in reversed(_DROPS):
        if name not in _existing_index_names(bind, table):
            op.create_index(name, table, list(columns))
    for name, table, _columns in reversed(_CREATES):
        if name in _existing_index_names(bind, table):
            op.drop_index(name, table_name=table)
