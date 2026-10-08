"""tenant tighten — every tenant-owned table whose key stays as it is made to require its tenant.

Each table in :data:`_PLAN` is brought to the shape listed there: ``tenant_id`` made ``NOT NULL`` with a named foreign
key to ``tenants``, its listing indexes rebuilt led by ``tenant_id`` (a hub-wide sweep's cursor index kept beside the
tenant-led one), and a plain ``tenant_id`` index where no index or key leads with it. Every index and foreign key the
revision creates carries its name; the keys and foreign keys it keeps leave as they were, unnamed ones included. One
batch per table under ``recreate="auto"`` tightens it (SQLite copies the table; other dialects alter it in place); no
table's key changes, so the batches run in any order.

``tenant_id`` was stamped on every row by ``20261008_1200_tenant_id_backfill``; while a table still holds a row without
one the upgrade refuses, naming it, before any table is touched. Re-running the upgrade skips every table already at
its target shape. The downgrade refuses while more than one tenant exists, naming them; with one it restores
:data:`_PRIOR` — the indexes and nullable ``tenant_id`` those tables had before.
Revision ID: 20261008_1400_tenant_tighten
Revises: 20261008_1300_tenant_keys
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_1400_tenant_tighten"
down_revision: str | None = "20261008_1300_tenant_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_COLUMN = "tenant_id"

# The target shape of every table this revision tightens — frozen here, never derived from ``schema.metadata``
# (``bzh:frozen-revisions``).
# --- M-05 begin ---
_Key = tuple[str | None, tuple[str, ...]]  # (name, columns)
_ForeignKey = tuple[str | None, tuple[str, ...], str, tuple[str, ...]]  # (name, columns, parent, parent columns)
_IndexPlan = tuple[str, tuple[str, ...], bool]  # (name, columns, unique)
# (table, primary key, uniques, foreign keys, indexes, tenant_id nullable)
_TablePlan = tuple[str, _Key, tuple[_Key, ...], tuple[_ForeignKey, ...], tuple[_IndexPlan, ...], bool]
_PLAN: tuple[_TablePlan, ...] = (
    (
        "artifacts",
        (None, ("artifact_id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_artifacts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_artifacts_chunk_id_node_id_epoch", ("chunk_id", "node_id", "epoch"), False),
            ("ix_artifacts_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "chunk_bounces",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_bounces_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_chunk_bounces_chunk_id_epoch", ("chunk_id", "epoch"), False),
            ("ix_chunk_bounces_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "chunk_completed",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_completed_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_chunk_completed_chunk_id", ("chunk_id",), False),
            ("ix_chunk_completed_completed_at_id", ("completed_at", "id"), False),
            ("ix_chunk_completed_tenant_completed_at_id", ("tenant_id", "completed_at", "id"), False),
        ),
        False,
    ),
    (
        "chunk_deleted",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_deleted_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_chunk_deleted_tenant_deleted_at_id", ("tenant_id", "deleted_at", "id"), False),),
        False,
    ),
    (
        "chunk_dependencies",
        (None, ("dependency_id",)),
        (),
        (
            (None, ("dependent_chunk_id",), "chunks", ("chunk_id",)),
            (None, ("prerequisite_chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_dependencies_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_chunk_dependencies_dependent_chunk_id", ("dependent_chunk_id",), False),
            ("ix_chunk_dependencies_prerequisite_chunk_id", ("prerequisite_chunk_id",), False),
            ("ix_chunk_dependencies_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "chunk_grouped",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            (None, ("grouped_into",), "chunks", ("chunk_id",)),
            ("fk_chunk_grouped_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_chunk_grouped_tenant_grouped_at_id", ("tenant_id", "grouped_at", "id"), False),),
        False,
    ),
    (
        "chunk_migrations",
        (None, ("migration_id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_migrations_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_chunk_migrations_chunk_id", ("chunk_id",), False),
            ("ix_chunk_migrations_recorded_at_migration_id", ("recorded_at", "migration_id"), False),
            (
                "ix_chunk_migrations_tenant_recorded_at_migration_id",
                ("tenant_id", "recorded_at", "migration_id"),
                False,
            ),
        ),
        False,
    ),
    (
        "chunk_pause_facts",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_pause_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_chunk_pause_facts_chunk_id", ("chunk_id",), False),
            ("ix_chunk_pause_facts_tenant_set_at_id", ("tenant_id", "set_at", "id"), False),
        ),
        False,
    ),
    (
        "chunk_promoted",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_promoted_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_chunk_promoted_chunk_id", ("chunk_id",), False),
            ("ix_chunk_promoted_tenant_promoted_at_id", ("tenant_id", "promoted_at", "id"), False),
        ),
        False,
    ),
    (
        "chunk_restarts",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_restarts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_chunk_restarts_chunk_id", ("chunk_id",), False),
            ("ix_chunk_restarts_recorded_at_id", ("recorded_at", "id"), False),
            ("ix_chunk_restarts_tenant_recorded_at_id", ("tenant_id", "recorded_at", "id"), False),
        ),
        False,
    ),
    (
        "chunk_stopped",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_stopped_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_chunk_stopped_chunk_id", ("chunk_id",), False),
            ("ix_chunk_stopped_stopped_at_id", ("stopped_at", "id"), False),
            ("ix_chunk_stopped_tenant_stopped_at_id", ("tenant_id", "stopped_at", "id"), False),
        ),
        False,
    ),
    (
        "chunks",
        (None, ("chunk_id",)),
        (),
        (
            (None, ("graph_id",), "graphs", ("graph_id",)),
            ("fk_chunks_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_chunks_tenant_minted_at_chunk_id", ("tenant_id", "minted_at", "chunk_id"), False),),
        False,
    ),
    (
        "close_intent_attempts",
        (None, ("id",)),
        (),
        (
            (None, ("intent_id",), "close_intents", ("id",)),
            ("fk_close_intent_attempts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_close_intent_attempts_intent_id", ("intent_id",), False),
            ("ix_close_intent_attempts_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "close_intents",
        (None, ("id",)),
        (("uq_close_intents_chunk_source_ref", ("chunk_id", "source", "ref")),),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_close_intents_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_close_intents_pending", ("retired_at",), False), ("ix_close_intents_tenant_id", ("tenant_id",), False)),
        False,
    ),
    (
        "config_changes",
        (None, ("id",)),
        (),
        (("fk_config_changes_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_config_changes_tenant_record", ("tenant_id", "record_kind", "record_key", "id"), False),),
        False,
    ),
    (
        "decision_resolutions",
        (None, ("decision_id",)),
        (),
        (
            (None, ("decision_id",), "decisions", ("decision_id",)),
            ("fk_decision_resolutions_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            (
                "ix_decision_resolutions_tenant_resolved_at_decision_id",
                ("tenant_id", "resolved_at", "decision_id"),
                False,
            ),
        ),
        False,
    ),
    (
        "decisions",
        (None, ("decision_id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_decisions_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_decisions_chunk_id", ("chunk_id",), False),
            ("ix_decisions_submitted_at_decision_id", ("submitted_at", "decision_id"), False),
            ("ix_decisions_tenant_submitted_at_decision_id", ("tenant_id", "submitted_at", "decision_id"), False),
        ),
        False,
    ),
    (
        "delivery_landed",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_delivery_landed_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_delivery_landed_chunk_id", ("chunk_id",), False),
            ("ix_delivery_landed_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "delivery_repo_landed",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_delivery_repo_landed_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_delivery_repo_landed_chunk_id", ("chunk_id",), False),
            ("ix_delivery_repo_landed_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "epoch_owners",
        (None, ("id",)),
        (("uq_epoch_owners_chunk_id_epoch", ("chunk_id", "epoch")),),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_epoch_owners_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_epoch_owners_recorded_at_id", ("recorded_at", "id"), False),
            ("ix_epoch_owners_tenant_recorded_at_id", ("tenant_id", "recorded_at", "id"), False),
        ),
        False,
    ),
    (
        "escalations",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_escalations_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_escalations_chunk_id", ("chunk_id",), False),
            ("ix_escalations_recorded_at_id", ("recorded_at", "id"), False),
            ("ix_escalations_tenant_recorded_at_id", ("tenant_id", "recorded_at", "id"), False),
        ),
        False,
    ),
    (
        "event_log",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_event_log_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_event_log_tenant_recorded_at", ("tenant_id", "recorded_at"), False),),
        False,
    ),
    (
        "finding_facts",
        (None, ("id",)),
        (),
        (
            (None, ("finding_id",), "findings", ("finding_id",)),
            ("fk_finding_facts_finding_set_id_finding_sets", ("finding_set_id",), "finding_sets", ("finding_set_id",)),
            ("fk_finding_facts_proposal_id_garden_proposals", ("proposal_id",), "garden_proposals", ("proposal_id",)),
            ("fk_finding_facts_superseded_by_findings", ("superseded_by",), "findings", ("finding_id",)),
            ("fk_finding_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_finding_facts_finding_id_id", ("finding_id", "id"), False),
            ("ix_finding_facts_finding_set_id", ("finding_set_id",), False),
            ("ix_finding_facts_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "finding_sets",
        (None, ("finding_set_id",)),
        ((None, ("artifact_id",)),),
        (
            (None, ("artifact_id",), "artifacts", ("artifact_id",)),
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            (None, ("scope_slug",), "scopes", ("slug",)),
            ("fk_finding_sets_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_finding_sets_chunk_id", ("chunk_id",), False),
            ("ix_finding_sets_tenant_routine_scope", ("tenant_id", "routine_name", "scope_slug"), False),
        ),
        False,
    ),
    (
        "findings",
        (None, ("finding_id",)),
        (),
        (
            (None, ("scope_slug",), "scopes", ("slug",)),
            ("fk_findings_raised_by_chunk_id", ("raised_by_chunk_id",), "chunks", ("chunk_id",)),
            ("fk_findings_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_findings_tenant_routine_class", ("tenant_id", "routine_name", "class"), False),
            ("ix_findings_tenant_routine_scope", ("tenant_id", "routine_name", "scope_slug"), False),
            ("ix_findings_tenant_scope_source", ("tenant_id", "scope_slug", "source"), False),
        ),
        False,
    ),
    (
        "forge_annotation_facts",
        (None, ("id",)),
        (),
        (("fk_forge_annotation_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_forge_annotation_facts_tenant_source_name_id", ("tenant_id", "source_name", "id"), False),),
        False,
    ),
    (
        "garden_proposal_findings",
        (None, ("proposal_id", "finding_id")),
        (),
        (
            (None, ("finding_id",), "findings", ("finding_id",)),
            (None, ("proposal_id",), "garden_proposals", ("proposal_id",)),
            ("fk_garden_proposal_findings_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_garden_proposal_findings_finding_id", ("finding_id",), False),
            ("ix_garden_proposal_findings_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "garden_proposals",
        (None, ("proposal_id",)),
        (),
        (("fk_garden_proposals_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (
            ("ix_garden_proposals_tenant_routine_class", ("tenant_id", "routine_name", "class"), False),
            ("ux_garden_proposals_source_artifact_ref", ("source_artifact_id", "ref"), True),
        ),
        False,
    ),
    (
        "graph_artifacts",
        (None, ("graph_id", "name")),
        (),
        (
            (None, ("graph_id",), "graphs", ("graph_id",)),
            ("fk_graph_artifacts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_graph_artifacts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "graph_choices",
        (None, ("choice_id",)),
        (),
        (
            (None, ("node_id",), "graph_nodes", ("node_id",)),
            ("fk_graph_choices_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_graph_choices_node_id", ("node_id",), False), ("ix_graph_choices_tenant_id", ("tenant_id",), False)),
        False,
    ),
    (
        "graph_edges",
        (None, ("edge_id",)),
        (),
        (
            (None, ("choice_id",), "graph_choices", ("choice_id",)),
            (None, ("from_node_id",), "graph_nodes", ("node_id",)),
            ("fk_graph_edges_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_graph_edges_from_node_id", ("from_node_id",), False),
            ("ix_graph_edges_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "graph_lifecycle_facts",
        (None, ("id",)),
        (),
        (
            (None, ("graph_id",), "graphs", ("graph_id",)),
            ("fk_graph_lifecycle_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_graph_lifecycle_facts_graph_id", ("graph_id",), False),
            ("ix_graph_lifecycle_facts_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "graph_nodes",
        (None, ("node_id",)),
        (),
        (
            (None, ("graph_id",), "graphs", ("graph_id",)),
            ("fk_graph_nodes_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_graph_nodes_graph_id", ("graph_id",), False), ("ix_graph_nodes_tenant_id", ("tenant_id",), False)),
        False,
    ),
    (
        "graph_policy_facts",
        (None, ("id",)),
        (),
        (
            (None, ("graph_id",), "graphs", ("graph_id",)),
            ("fk_graph_policy_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_graph_policy_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "graph_sessions",
        (None, ("graph_id", "name")),
        (),
        (
            (None, ("graph_id",), "graphs", ("graph_id",)),
            ("fk_graph_sessions_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_graph_sessions_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "hub_exec_slot",
        (None, ("slot_id",)),
        (),
        (
            (None, ("holder_chunk_id",), "chunks", ("chunk_id",)),
            ("fk_hub_exec_slot_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_hub_exec_slot_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "hub_node_poll",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_hub_node_poll_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_hub_node_poll_chunk_id", ("chunk_id",), False), ("ix_hub_node_poll_tenant_id", ("tenant_id",), False)),
        False,
    ),
    (
        "invitation_facts",
        (None, ("id",)),
        (("uq_invitation_facts_invitation_id", ("invitation_id",)),),
        (
            ("fk_invitation_facts_invitation_id", ("invitation_id",), "invitations", ("invitation_id",)),
            ("fk_invitation_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_invitation_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "invitations",
        (None, ("invitation_id",)),
        (),
        (("fk_invitations_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_invitations_tenant_id", ("tenant_id",), False), ("ix_invitations_token_hash", ("token_hash",), False)),
        False,
    ),
    (
        "lease_facts",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_lease_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_lease_facts_chunk_id_epoch", ("chunk_id", "epoch"), False),
            ("ix_lease_facts_minted_at", ("minted_at",), False),
            ("ix_lease_facts_tenant_minted_at", ("tenant_id", "minted_at"), False),
        ),
        False,
    ),
    (
        "queue_positions",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_queue_positions_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_queue_positions_chunk_id", ("chunk_id",), False),
            ("ix_queue_positions_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "requeues",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_requeues_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_requeues_chunk_id", ("chunk_id",), False),
            ("ix_requeues_tenant_requeued_at_id", ("tenant_id", "requeued_at", "id"), False),
        ),
        False,
    ),
    (
        "route_created",
        (None, ("route_id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_route_created_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_route_created_chunk_id", ("chunk_id",), False),
            ("ix_route_created_runner_id", ("runner_id",), False),
            ("ix_route_created_tenant_created_at_route_id", ("tenant_id", "created_at", "route_id"), False),
        ),
        False,
    ),
    (
        "route_environments",
        (None, ("id",)),
        (),
        (
            (None, ("route_id",), "route_created", ("route_id",)),
            ("fk_route_environments_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_route_environments_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "route_released",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_route_released_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_route_released_chunk_id", ("chunk_id",), False),
            ("ix_route_released_released_at_id", ("released_at", "id"), False),
            ("ix_route_released_tenant_released_at_id", ("tenant_id", "released_at", "id"), False),
        ),
        False,
    ),
    (
        "route_token_minted",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_route_token_minted_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_route_token_minted_chunk_id", ("chunk_id",), False),
            ("ix_route_token_minted_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "routine_lifecycle_facts",
        (None, ("id",)),
        (),
        (
            (None, ("routine_id",), "routines", ("routine_id",)),
            ("fk_routine_lifecycle_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_routine_lifecycle_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "routine_scopes",
        (None, ("routine_id", "scope_slug")),
        (),
        (
            (None, ("routine_id",), "routines", ("routine_id",)),
            (None, ("scope_slug",), "scopes", ("slug",)),
            ("fk_routine_scopes_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_routine_scopes_scope_slug", ("scope_slug",), False),
            ("ix_routine_scopes_tenant_id", ("tenant_id",), False),
        ),
        False,
    ),
    (
        "runner_external_usage",
        ("pk_runner_external_usage", ("runner_id", "slug")),
        (),
        (("fk_runner_external_usage_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_runner_external_usage_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "runner_external_usage_misses",
        (None, ("runner_id", "slug")),
        (),
        (("fk_runner_external_usage_misses_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_runner_external_usage_misses_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "runner_high_water",
        (None, ("runner_id",)),
        (),
        (("fk_runner_high_water_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_runner_high_water_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "runner_lifecycle_facts",
        (None, ("id",)),
        (),
        (
            (None, ("runner_id",), "runner_registrations", ("runner_id",)),
            ("fk_runner_lifecycle_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_runner_lifecycle_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "runner_local_pause_facts",
        (None, ("id",)),
        (),
        (("fk_runner_local_pause_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_runner_local_pause_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "runner_pause_facts",
        (None, ("id",)),
        (),
        (
            (None, ("runner_id",), "runner_registrations", ("runner_id",)),
            ("fk_runner_pause_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_runner_pause_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "runner_registrations",
        (None, ("runner_id",)),
        (),
        (("fk_runner_registrations_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (
            ("ix_runner_registrations_tenant_id", ("tenant_id",), False),
            ("ix_runner_registrations_token_hash", ("token_hash",), False),
        ),
        False,
    ),
    (
        "runner_token_revocations",
        (None, ("id",)),
        (),
        (
            (None, ("runner_id",), "runner_registrations", ("runner_id",)),
            ("fk_runner_token_revocations_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_runner_token_revocations_tenant_id", ("tenant_id",), False),
            ("ix_runner_token_revocations_token_hash", ("token_hash",), False),
        ),
        False,
    ),
    (
        "scope_lifecycle_facts",
        (None, ("id",)),
        (),
        (
            (None, ("slug",), "scopes", ("slug",)),
            ("fk_scope_lifecycle_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_scope_lifecycle_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "scopes",
        (None, ("slug",)),
        (),
        (("fk_scopes_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_scopes_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "transcript_event_drops",
        (None, ("id",)),
        (),
        (("fk_transcript_event_drops_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (
            ("ix_transcript_event_drops_chunk_id_epoch", ("chunk_id", "epoch"), False),
            ("ix_transcript_event_drops_dropped_at_segment_id", ("dropped_at", "segment_id"), False),
            (
                "ix_transcript_event_drops_tenant_dropped_at_segment_id",
                ("tenant_id", "dropped_at", "segment_id"),
                False,
            ),
        ),
        False,
    ),
    (
        "transcript_high_water",
        (None, ("runner_id",)),
        (),
        (("fk_transcript_high_water_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_transcript_high_water_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "transitions",
        (None, ("transition_id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_transitions_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_transitions_chunk_id_epoch", ("chunk_id", "epoch"), False),
            ("ix_transitions_recorded_at_transition_id", ("recorded_at", "transition_id"), False),
            ("ix_transitions_tenant_recorded_at_transition_id", ("tenant_id", "recorded_at", "transition_id"), False),
            ("ix_transitions_to_node_id", ("to_node_id",), False),
        ),
        False,
    ),
    (
        "usage_facts",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_usage_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_usage_facts_chunk_id", ("chunk_id",), False),
            ("ix_usage_facts_node_id", ("node_id",), False),
            ("ix_usage_facts_recorded_at_id", ("recorded_at", "id"), False),
            ("ix_usage_facts_tenant_recorded_at_id", ("tenant_id", "recorded_at", "id"), False),
        ),
        False,
    ),
    (
        "work_item_closures",
        (None, ("id",)),
        (("uq_work_item_closures_chunk_source_ref_outcome", ("chunk_id", "source", "ref", "outcome")),),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_work_item_closures_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_work_item_closures_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "work_item_runs",
        (None, ("work_item_id",)),
        (),
        (
            (None, ("scope_slug",), "scopes", ("slug",)),
            (None, ("work_item_id",), "work_items", ("work_item_id",)),
            ("fk_work_item_runs_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_work_item_runs_tenant_id", ("tenant_id",), False),),
        False,
    ),
)
# --- M-05 end ---

# The same tables as 20261008_1300_tenant_keys left them — the shape the downgrade restores.
_PRIOR: tuple[_TablePlan, ...] = (
    (
        "artifacts",
        (None, ("artifact_id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_artifacts_chunk_id_node_id_epoch", ("chunk_id", "node_id", "epoch"), False),),
        True,
    ),
    (
        "chunk_bounces",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_chunk_bounces_chunk_id_epoch", ("chunk_id", "epoch"), False),),
        True,
    ),
    (
        "chunk_completed",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_chunk_completed_chunk_id", ("chunk_id",), False),
            ("ix_chunk_completed_completed_at_id", ("completed_at", "id"), False),
        ),
        True,
    ),
    (
        "chunk_deleted",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_chunk_deleted_deleted_at_id", ("deleted_at", "id"), False),),
        True,
    ),
    (
        "chunk_dependencies",
        (None, ("dependency_id",)),
        (),
        (
            (None, ("dependent_chunk_id",), "chunks", ("chunk_id",)),
            (None, ("prerequisite_chunk_id",), "chunks", ("chunk_id",)),
        ),
        (
            ("ix_chunk_dependencies_dependent_chunk_id", ("dependent_chunk_id",), False),
            ("ix_chunk_dependencies_prerequisite_chunk_id", ("prerequisite_chunk_id",), False),
        ),
        True,
    ),
    (
        "chunk_grouped",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)), (None, ("grouped_into",), "chunks", ("chunk_id",))),
        (("ix_chunk_grouped_grouped_at_id", ("grouped_at", "id"), False),),
        True,
    ),
    (
        "chunk_migrations",
        (None, ("migration_id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_chunk_migrations_chunk_id", ("chunk_id",), False),
            ("ix_chunk_migrations_recorded_at_migration_id", ("recorded_at", "migration_id"), False),
        ),
        True,
    ),
    (
        "chunk_pause_facts",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_chunk_pause_facts_chunk_id", ("chunk_id",), False),
            ("ix_chunk_pause_facts_set_at_id", ("set_at", "id"), False),
        ),
        True,
    ),
    (
        "chunk_promoted",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_chunk_promoted_chunk_id", ("chunk_id",), False),
            ("ix_chunk_promoted_promoted_at_id", ("promoted_at", "id"), False),
        ),
        True,
    ),
    (
        "chunk_restarts",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_chunk_restarts_chunk_id", ("chunk_id",), False),
            ("ix_chunk_restarts_recorded_at_id", ("recorded_at", "id"), False),
        ),
        True,
    ),
    (
        "chunk_stopped",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_chunk_stopped_chunk_id", ("chunk_id",), False),
            ("ix_chunk_stopped_stopped_at_id", ("stopped_at", "id"), False),
        ),
        True,
    ),
    (
        "chunks",
        (None, ("chunk_id",)),
        (),
        ((None, ("graph_id",), "graphs", ("graph_id",)),),
        (("ix_chunks_minted_at_chunk_id", ("minted_at", "chunk_id"), False),),
        True,
    ),
    (
        "close_intent_attempts",
        (None, ("id",)),
        (),
        ((None, ("intent_id",), "close_intents", ("id",)),),
        (("ix_close_intent_attempts_intent_id", ("intent_id",), False),),
        True,
    ),
    (
        "close_intents",
        (None, ("id",)),
        (("uq_close_intents_chunk_source_ref", ("chunk_id", "source", "ref")),),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_close_intents_pending", ("retired_at",), False),),
        True,
    ),
    (
        "config_changes",
        (None, ("id",)),
        (),
        (),
        (("ix_config_changes_record", ("record_kind", "record_key", "id"), False),),
        True,
    ),
    (
        "decision_resolutions",
        (None, ("decision_id",)),
        (),
        ((None, ("decision_id",), "decisions", ("decision_id",)),),
        (("ix_decision_resolutions_resolved_at_decision_id", ("resolved_at", "decision_id"), False),),
        True,
    ),
    (
        "decisions",
        (None, ("decision_id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_decisions_chunk_id", ("chunk_id",), False),
            ("ix_decisions_submitted_at_decision_id", ("submitted_at", "decision_id"), False),
        ),
        True,
    ),
    (
        "delivery_landed",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_delivery_landed_chunk_id", ("chunk_id",), False),),
        True,
    ),
    (
        "delivery_repo_landed",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_delivery_repo_landed_chunk_id", ("chunk_id",), False),),
        True,
    ),
    (
        "epoch_owners",
        (None, ("id",)),
        (("uq_epoch_owners_chunk_id_epoch", ("chunk_id", "epoch")),),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_epoch_owners_recorded_at_id", ("recorded_at", "id"), False),),
        True,
    ),
    (
        "escalations",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_escalations_chunk_id", ("chunk_id",), False),
            ("ix_escalations_recorded_at_id", ("recorded_at", "id"), False),
        ),
        True,
    ),
    (
        "event_log",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_event_log_recorded_at", ("recorded_at",), False),),
        True,
    ),
    (
        "finding_facts",
        (None, ("id",)),
        (),
        (
            (None, ("finding_id",), "findings", ("finding_id",)),
            ("fk_finding_facts_finding_set_id_finding_sets", ("finding_set_id",), "finding_sets", ("finding_set_id",)),
            ("fk_finding_facts_proposal_id_garden_proposals", ("proposal_id",), "garden_proposals", ("proposal_id",)),
            ("fk_finding_facts_superseded_by_findings", ("superseded_by",), "findings", ("finding_id",)),
        ),
        (
            ("ix_finding_facts_finding_id_id", ("finding_id", "id"), False),
            ("ix_finding_facts_finding_set_id", ("finding_set_id",), False),
        ),
        True,
    ),
    (
        "finding_sets",
        (None, ("finding_set_id",)),
        ((None, ("artifact_id",)),),
        (
            (None, ("artifact_id",), "artifacts", ("artifact_id",)),
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            (None, ("scope_slug",), "scopes", ("slug",)),
        ),
        (
            ("ix_finding_sets_chunk_id", ("chunk_id",), False),
            ("ix_finding_sets_routine_scope", ("routine_name", "scope_slug"), False),
        ),
        True,
    ),
    (
        "findings",
        (None, ("finding_id",)),
        (),
        (
            (None, ("scope_slug",), "scopes", ("slug",)),
            ("fk_findings_raised_by_chunk_id", ("raised_by_chunk_id",), "chunks", ("chunk_id",)),
        ),
        (
            ("ix_findings_routine_class", ("routine_name", "class"), False),
            ("ix_findings_routine_scope", ("routine_name", "scope_slug"), False),
            ("ix_findings_scope_source", ("scope_slug", "source"), False),
        ),
        True,
    ),
    (
        "forge_annotation_facts",
        (None, ("id",)),
        (),
        (),
        (("ix_forge_annotation_facts_source_name_id", ("source_name", "id"), False),),
        True,
    ),
    (
        "garden_proposal_findings",
        (None, ("proposal_id", "finding_id")),
        (),
        (
            (None, ("finding_id",), "findings", ("finding_id",)),
            (None, ("proposal_id",), "garden_proposals", ("proposal_id",)),
        ),
        (("ix_garden_proposal_findings_finding_id", ("finding_id",), False),),
        True,
    ),
    (
        "garden_proposals",
        (None, ("proposal_id",)),
        (),
        (),
        (
            ("ix_garden_proposals_routine_class", ("routine_name", "class"), False),
            ("ux_garden_proposals_source_artifact_ref", ("source_artifact_id", "ref"), True),
        ),
        True,
    ),
    ("graph_artifacts", (None, ("graph_id", "name")), (), ((None, ("graph_id",), "graphs", ("graph_id",)),), (), True),
    (
        "graph_choices",
        (None, ("choice_id",)),
        (),
        ((None, ("node_id",), "graph_nodes", ("node_id",)),),
        (("ix_graph_choices_node_id", ("node_id",), False),),
        True,
    ),
    (
        "graph_edges",
        (None, ("edge_id",)),
        (),
        (
            (None, ("choice_id",), "graph_choices", ("choice_id",)),
            (None, ("from_node_id",), "graph_nodes", ("node_id",)),
        ),
        (("ix_graph_edges_from_node_id", ("from_node_id",), False),),
        True,
    ),
    (
        "graph_lifecycle_facts",
        (None, ("id",)),
        (),
        ((None, ("graph_id",), "graphs", ("graph_id",)),),
        (("ix_graph_lifecycle_facts_graph_id", ("graph_id",), False),),
        True,
    ),
    (
        "graph_nodes",
        (None, ("node_id",)),
        (),
        ((None, ("graph_id",), "graphs", ("graph_id",)),),
        (("ix_graph_nodes_graph_id", ("graph_id",), False),),
        True,
    ),
    ("graph_policy_facts", (None, ("id",)), (), ((None, ("graph_id",), "graphs", ("graph_id",)),), (), True),
    ("graph_sessions", (None, ("graph_id", "name")), (), ((None, ("graph_id",), "graphs", ("graph_id",)),), (), True),
    ("hub_exec_slot", (None, ("slot_id",)), (), ((None, ("holder_chunk_id",), "chunks", ("chunk_id",)),), (), True),
    (
        "hub_node_poll",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_hub_node_poll_chunk_id", ("chunk_id",), False),),
        True,
    ),
    (
        "invitation_facts",
        (None, ("id",)),
        (("uq_invitation_facts_invitation_id", ("invitation_id",)),),
        (
            ("fk_invitation_facts_invitation_id", ("invitation_id",), "invitations", ("invitation_id",)),
            ("fk_invitation_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_invitation_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "invitations",
        (None, ("invitation_id",)),
        (),
        (("fk_invitations_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_invitations_tenant_id", ("tenant_id",), False), ("ix_invitations_token_hash", ("token_hash",), False)),
        False,
    ),
    (
        "lease_facts",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_lease_facts_chunk_id_epoch", ("chunk_id", "epoch"), False),
            ("ix_lease_facts_minted_at", ("minted_at",), False),
        ),
        True,
    ),
    (
        "queue_positions",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_queue_positions_chunk_id", ("chunk_id",), False),),
        True,
    ),
    (
        "requeues",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_requeues_chunk_id", ("chunk_id",), False), ("ix_requeues_requeued_at_id", ("requeued_at", "id"), False)),
        True,
    ),
    (
        "route_created",
        (None, ("route_id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_route_created_chunk_id", ("chunk_id",), False),
            ("ix_route_created_created_at_route_id", ("created_at", "route_id"), False),
            ("ix_route_created_runner_id", ("runner_id",), False),
        ),
        True,
    ),
    ("route_environments", (None, ("id",)), (), ((None, ("route_id",), "route_created", ("route_id",)),), (), True),
    (
        "route_released",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_route_released_chunk_id", ("chunk_id",), False),
            ("ix_route_released_released_at_id", ("released_at", "id"), False),
        ),
        True,
    ),
    (
        "route_token_minted",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (("ix_route_token_minted_chunk_id", ("chunk_id",), False),),
        True,
    ),
    ("routine_lifecycle_facts", (None, ("id",)), (), ((None, ("routine_id",), "routines", ("routine_id",)),), (), True),
    (
        "routine_scopes",
        (None, ("routine_id", "scope_slug")),
        (),
        ((None, ("routine_id",), "routines", ("routine_id",)), (None, ("scope_slug",), "scopes", ("slug",))),
        (("ix_routine_scopes_scope_slug", ("scope_slug",), False),),
        True,
    ),
    ("runner_external_usage", ("pk_runner_external_usage", ("runner_id", "slug")), (), (), (), True),
    ("runner_external_usage_misses", (None, ("runner_id", "slug")), (), (), (), True),
    ("runner_high_water", (None, ("runner_id",)), (), (), (), True),
    (
        "runner_lifecycle_facts",
        (None, ("id",)),
        (),
        ((None, ("runner_id",), "runner_registrations", ("runner_id",)),),
        (),
        True,
    ),
    ("runner_local_pause_facts", (None, ("id",)), (), (), (), True),
    (
        "runner_pause_facts",
        (None, ("id",)),
        (),
        ((None, ("runner_id",), "runner_registrations", ("runner_id",)),),
        (),
        True,
    ),
    (
        "runner_registrations",
        (None, ("runner_id",)),
        (),
        (),
        (("ix_runner_registrations_token_hash", ("token_hash",), False),),
        True,
    ),
    (
        "runner_token_revocations",
        (None, ("id",)),
        (),
        ((None, ("runner_id",), "runner_registrations", ("runner_id",)),),
        (("ix_runner_token_revocations_token_hash", ("token_hash",), False),),
        True,
    ),
    ("scope_lifecycle_facts", (None, ("id",)), (), ((None, ("slug",), "scopes", ("slug",)),), (), True),
    ("scopes", (None, ("slug",)), (), (), (), True),
    (
        "transcript_event_drops",
        (None, ("id",)),
        (),
        (),
        (
            ("ix_transcript_event_drops_chunk_id_epoch", ("chunk_id", "epoch"), False),
            ("ix_transcript_event_drops_dropped_at_segment_id", ("dropped_at", "segment_id"), False),
        ),
        True,
    ),
    ("transcript_high_water", (None, ("runner_id",)), (), (), (), True),
    (
        "transitions",
        (None, ("transition_id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_transitions_chunk_id_epoch", ("chunk_id", "epoch"), False),
            ("ix_transitions_recorded_at_transition_id", ("recorded_at", "transition_id"), False),
            ("ix_transitions_to_node_id", ("to_node_id",), False),
        ),
        True,
    ),
    (
        "usage_facts",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_usage_facts_chunk_id", ("chunk_id",), False),
            ("ix_usage_facts_node_id", ("node_id",), False),
            ("ix_usage_facts_recorded_at_id", ("recorded_at", "id"), False),
        ),
        True,
    ),
    (
        "work_item_closures",
        (None, ("id",)),
        (("uq_work_item_closures_chunk_source_ref_outcome", ("chunk_id", "source", "ref", "outcome")),),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (),
        True,
    ),
    (
        "work_item_runs",
        (None, ("work_item_id",)),
        (),
        ((None, ("scope_slug",), "scopes", ("slug",)), (None, ("work_item_id",), "work_items", ("work_item_id",))),
        (),
        True,
    ),
)


# A table's shape as compared: primary key, then unique constraints, foreign keys and indexes as sets, then whether
# ``tenant_id`` is nullable.
_Shape = tuple[_Key, frozenset[_Key], frozenset[_ForeignKey], frozenset[_IndexPlan], bool]

_tenants = sa.table("tenants", sa.column("tenant_id", sa.String))


def _tenant_ids(bind: sa.Connection) -> list[str]:
    return list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())


def _wanted(plan: _TablePlan) -> _Shape:
    _, primary_key, uniques, foreign_keys, indexes, nullable = plan
    return primary_key, frozenset(uniques), frozenset(foreign_keys), frozenset(indexes), nullable


def _shape(bind: sa.Connection, table: str) -> _Shape:
    inspector = sa.inspect(bind)
    primary_key = inspector.get_pk_constraint(table)
    tenant_nullable = next(c["nullable"] for c in inspector.get_columns(table) if c["name"] == _TENANT_COLUMN)
    return (
        (primary_key.get("name"), tuple(primary_key["constrained_columns"])),
        frozenset((u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints(table)),
        frozenset(
            (fk.get("name"), tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in inspector.get_foreign_keys(table)
        ),
        frozenset(
            (str(i["name"]), tuple(str(c) for c in i["column_names"]), bool(i["unique"]))
            for i in inspector.get_indexes(table)
            if not i.get("duplicates_constraint")
        ),
        bool(tenant_nullable),
    )


def _refuse_unstamped(bind: sa.Connection, plans: tuple[_TablePlan, ...]) -> None:
    """Refuse while a table whose ``tenant_id`` becomes required holds a row without one."""
    for table, *_, nullable in plans:
        stub = sa.table(table, sa.column(_TENANT_COLUMN, sa.String))
        unstamped = sa.select(sa.literal(1)).select_from(stub).where(stub.c[_TENANT_COLUMN].is_(None)).limit(1)
        if not nullable and bind.execute(unstamped).first():
            raise RuntimeError(
                f"{table} holds rows without a tenant_id; the tenant_id backfill stamps every row before it is required"
            )


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a batch left behind when its run stopped part way.

    SQLite creates the copy outside the transaction the rest of the batch runs in, so a stopped run can keep it while
    the table itself is rolled back; the name is the one Alembic's batch gives it.
    """
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, the structure every batch copies from: the copy keeps each constraint it does
    not touch exactly as the store holds it — an unnamed ``CHECK``, an unnamed foreign key, a named primary key."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def _tighten(bind: sa.Connection, plan: _TablePlan) -> None:
    """One batch bringing ``plan[0]`` to ``plan``: its ``tenant_id`` nullability, its named foreign keys, its indexes.

    Its key, its unique constraints and its unnamed foreign keys are the same on both sides of this revision; a store
    whose table differs from the plan in any of them is not one this revision can reach, and is refused untouched.
    """
    table = plan[0]
    current, wanted = _shape(bind, table), _wanted(plan)
    if current == wanted:
        return
    primary_key, uniques, foreign_keys, indexes, nullable = current
    new_primary_key, new_uniques, new_foreign_keys, new_indexes, new_nullable = wanted
    if (
        primary_key != new_primary_key
        or uniques != new_uniques
        or any(fk[0] is None for fk in foreign_keys ^ new_foreign_keys)
    ):
        raise RuntimeError(f"{table} carries a key the tenant tighten does not expect; it changes no table's keys")
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        for name, *_ in sorted(foreign_keys - new_foreign_keys, key=str):
            batch.drop_constraint(str(name), type_="foreignkey")
        for name, _, _ in sorted(indexes - new_indexes):
            batch.drop_index(name)
        if nullable != new_nullable:
            batch.alter_column(_TENANT_COLUMN, existing_type=sa.String(), nullable=new_nullable)
        for name, columns, parent, parent_columns in sorted(new_foreign_keys - foreign_keys, key=str):
            batch.create_foreign_key(name, parent, list(columns), list(parent_columns))
        for name, columns, unique in sorted(new_indexes - indexes):
            batch.create_index(name, list(columns), unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_unstamped(bind, _PLAN)
    for plan in _PLAN:
        _tighten(bind, plan)


def downgrade() -> None:
    bind = op.get_bind()
    tenants = _tenant_ids(bind)
    if len(tenants) > 1:
        raise RuntimeError(f"delete every tenant but one before downgrading; the store holds {', '.join(tenants)}")
    for prior in _PRIOR:
        _tighten(bind, prior)
