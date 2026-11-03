"""hub store baseline — the history up to v0.2.0 as one revision.

Creates the schema v0.2.0 declares, and inserts the rows its history seeds. An id minted by a seeded row and the instant a seeded row is stamped with are minted and stamped afresh when this runs, each id once wherever it recurs; every other value is the one the history wrote. A store at a revision in ``ABSORBED`` is refused, naming ``BASELINE_FLOOR``.

Revision ID: 20261018_1200_project_color
Revises:
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.migrations import migration_clock
from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261018_1200_project_color"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASELINE_FLOOR = "v0.2.0"
ABSORBED = frozenset(
    {
        "20260713_1112_hub_initial",
        "20260713_1218_hub_walking_skeleton",
        "20260713_1424_hub_escalation_takeover",
        "20260713_1635_hub_runner_high_water",
        "20260713_1716_hub_graph_node_produces_checks",
        "20260713_1801_hub_questions_and_answers",
        "20260713_1803_hub_gate_decisions",
        "20260713_1946_hub_queue_shaping",
        "20260713_1947_hub_runner_registry",
        "20260714_0819_hub_delivery_pr_facts",
        "20260715_1817_hub_chunk_promoted",
        "20260716_1511_hub_runner_local_pause",
        "20260716_1512_hub_pm_pointer_source_ref",
        "20260716_2206_hub_pr_opened_idempotent",
        "20260716_2207_hub_route_seq_tiebreak",
        "20260717_0446_hub_chunk_pause_facts",
        "20260717_2318_hub_chunk_model_selection",
        "20260717_2330_hub_usage_facts",
        "20260717_2345_hub_chunk_bounces",
        "20260717_2359_hub_command_nodes",
        "20260718_0030_hub_node_poll",
        "20260718_0930_hub_runner_local_pause_reason",
        "20260718_1130_hub_runner_token",
        "20260718_1200_hub_route_token_minted",
        "20260718_1215_hub_transition_graph_id",
        "20260718_1220_hub_edge_target_graph_model",
        "20260718_1225_hub_chunk_migrations",
        "20260718_1300_hub_runner_env_capacity",
        "20260719_0900_hub_graph_lifecycle_facts",
        "20260719_2000_hub_chunk_stopped_by",
        "20260720_1000_hub_chunk_intended_migration",
        "20260721_1000_hub_escalation_decision_id",
        "20260721_1008_hub_graph_node_session_source",
        "20260721_1100_hub_auth_identity_spine",
        "20260721_1200_hub_auth_oauth",
        "20260721_1300_hub_auth_superuser_bootstrap",
        "20260721_1400_hub_runner_redirect_uris",
        "20260721_1500_hub_cli_auth_state_user",
        "20260721_1600_hub_event_log",
        "20260722_1200_hub_artifact_forge",
        "20260725_1200_hub_graph_checks_gating",
        "20260726_1200_hub_chunk_work_refs_rename",
        "20260728_1200_hub_graph_policy_facts",
        "20260728_1230_hub_chunk_migration_source",
        "20260728_1400_hub_graph_sessions",
        "20260728_1410_hub_chunk_defaults",
        "20260731_1200_hub_transitions_recorded_at",
        "20260801_1400_hub_work_item_closures",
        "20260801_1600_hub_runner_external_usage",
        "20260803_1000_hub_escalation_wrapped_takeover",
        "20260809_1200_hub_transcript_segments",
        "20260809_1800_hub_transcript_segment_record_truncated",
        "20260811_0905_hub_transcript_segment_supersedes",
        "20260812_1200_hub_transcript_events",
        "20260812_1300_hub_transcript_events_subject_tool",
        "20260817_0900_hub_graph_artifacts",
        "20260818_0900_hub_graph_sessions_compaction_window",
        "20260818_2100_chunk_completed",
        "20260819_0900_chunk_restarts",
        "20260819_1100_work_items",
        "20260819_2200_chunk_restart_from_graph",
        "20260825_1000_chunk_deleted",
        "20260825_1050_graph_node_proposes_work_items",
        "20260825_1100_work_item_proposals",
        "20260825_1150_work_item_proposals_runner_id",
        "20260825_1200_work_item_materializations",
        "20260825_1250_hub_transitions_to_node_id",
        "20260825_1300_work_item_strikes",
        "20260826_0900_close_intents",
        "20260826_0930_close_intents_backfill",
        "20260828_1000_scopes_and_routines",
        "20260829_0900_findings_and_proposals",
        "20260829_1930_fact_tables_chunk_id_index",
        "20260830_1835_work_item_runs",
        "20260830_2015_garden_proposals_source_artifact",
        "20260831_0900_work_items_routine_run",
        "20260831_0930_finding_sets_routine_name",
        "20260831_0945_garden_proposal_closures",
        "20260831_1030_finding_exits",
        "20260831_1100_gpc_item_index",
        "20260901_0900_finding_facts_finding_set_id",
        "20260902_0900_chunk_dependencies",
        "20260902_1000_finding_facts_ref",
        "20260905_1100_hub_runner_external_usage_slug",
        "20260906_1130_routine_scopes_join",
        "20260907_0900_drop_node_mode",
        "20260907_1000_event_log_runner_id_nullable",
        "20260913_1000_transcript_segments_content_digest",
        "20260913_1200_close_intent_attempts",
        "20260913_1300_hub_store_hot_path_indexes",
        "20260914_1000_hub_harness_provenance",
        "20260916_1000_hub_authored_harnesses",
        "20260916_1100_hub_runner_capabilities",
        "20260919_1000_hub_transcript_provenance",
        "20260920_1100_hub_usage_harness_provenance",
        "20260920_1200_finding_delivered_state",
        "20260922_1000_review_findings",
        "20260922_1100_runner_external_usage_misses",
        "20260922_1200_hub_usage_estimated_cost",
        "20260923_1000_routine_lifecycle_facts",
        "20260926_1000_hub_runner_declared_subscriptions",
        "20260926_1000_operator_garden_proposals",
        "20260928_1000_queue_positions_chunk_id_index",
        "20260928_1100_runner_retirement",
        "20260929_1000_decision_imposed_by_runner",
        "20260929_1100_drop_open_pr_facts",
        "20260930_1000_artifact_seq",
        "20260930_1000_epoch_owners",
        "20261001_1000_trace_cursor",
        "20261003_0900_egress_cursor",
        "20261003_1200_event_drops",
        "20261003_1300_hub_transcript_segment_spawn_cwd",
        "20261003_1400_escalation_cause",
        "20261004_1000_hub_secrets",
        "20261004_1100_work_source_records",
        "20261004_1200_repository_records",
        "20261004_1300_egress_events",
        "20261004_1400_forge_annotation_facts",
        "20261005_0900_keyed_locks",
        "20261005_1000_scope_routine_revisions",
        "20261006_0900_config_import_facts",
        "20261006_1200_runner_minted_ids",
        "20261008_1000_drop_work_item_proposals",
        "20261008_1100_tenants",
        "20261008_1200_tenant_id_backfill",
        "20261008_1300_tenant_keys",
        "20261008_1400_tenant_tighten",
        "20261008_1500_scope_surrogate",
        "20261008_1600_hub_event_log_move",
        "20261008_1650_identity_tables",
        "20261008_1700_tenant_identity_move",
        "20261008_1710_auth_state_invitation",
        "20261008_1720_tenant_deletion_closed_at",
        "20261008_1800_projects",
        "20261008_1810_project_read_indexes",
        "20261008_1900_project_columns",
        "20261011_1300_chunk_recorded_at_indexes",
        "20261012_0900_repository_identity",
        "20261012_1000_hosts",
        "20261012_1100_runner_bindings",
        "20261012_1500_work_source_identity",
        "20261013_0900_host_fact_lane",
        "20261014_0900_question_decision_project",
        "20261016_0900_chunk_demoted",
        "20261018_0900_decision_check_results",
    }
)

# Restated, not imported, from ``blizzard.foundation.ids`` (``bzh:frozen-revisions``).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26  # 48 bits of millisecond timestamp, then 80 random bits
_RANDOM_BITS = 80
_MINTED_ID = re.compile(r"(?<![A-Za-z0-9])([a-z]{2,6})_[0-9A-HJKMNP-TV-Z]{26}(?![A-Za-z0-9])")

_seed_tenants = sa.table('tenants', sa.column('tenant_id'), sa.column('name'), sa.column('created_at', UtcDateTime()), sa.column('created_by'))
_seed_projects = sa.table('projects', sa.column('project_id'), sa.column('slug'), sa.column('name'), sa.column('description'), sa.column('revision'), sa.column('created_at', UtcDateTime()), sa.column('tenant_id'), sa.column('color'))
_seed_config_changes = sa.table('config_changes', sa.column('id'), sa.column('recorded_at', UtcDateTime()), sa.column('actor'), sa.column('door'), sa.column('record_kind'), sa.column('record_key'), sa.column('revision'), sa.column('op'), sa.column('diff'), sa.column('apply_id'), sa.column('tenant_id'), sa.column('project_id'))


def _encode(value: int) -> str:
    chars: list[str] = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _mint(prefix: str, at: datetime) -> str:
    millis = int(at.timestamp() * 1000)
    randomness = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
    return f"{prefix}_{_encode((millis << _RANDOM_BITS) | randomness)}"


def _regenerate(text: str, minted: dict[str, str], at: datetime) -> str:
    """``text`` with each id it holds replaced by a fresh one, the same fresh one wherever the same id recurs."""
    return _MINTED_ID.sub(lambda found: minted.setdefault(found.group(0), _mint(found.group(1), at)), text)


def upgrade() -> None:
    op.create_table('auth_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('subject', sa.String(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('auth_state',
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('provider_name', sa.String(), nullable=False),
    sa.Column('return_to', sa.String(), nullable=False),
    sa.Column('code_challenge', sa.String(), nullable=True),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('expires_at', UtcDateTime(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=True),
    sa.Column('invitation_token_hash', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('state')
    )
    op.create_table('config_import_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('imported_at', UtcDateTime(), nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('read', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('egress_cursor',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('dataset', sa.String(), nullable=False),
    sa.Column('position_at', UtcDateTime(), nullable=True),
    sa.Column('chunk_id', sa.String(), nullable=True),
    sa.Column('epoch', sa.Integer(), nullable=True),
    sa.Column('decision_id', sa.String(), nullable=True),
    sa.Column('usage_recorded_at', UtcDateTime(), nullable=False),
    sa.Column('usage_id', sa.Integer(), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('files', sa.Text(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('segment_id', sa.String(), nullable=True),
    sa.Column('extractor_version', sa.String(), nullable=True),
    sa.Column('events_tenant_id', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_egress_cursor_dataset_recorded_at_id', 'egress_cursor', ['dataset', 'recorded_at', 'id'], unique=False)
    op.create_table('hub_event_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('severity', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_hub_event_log_kind_recorded_at', 'hub_event_log', ['kind', 'recorded_at'], unique=False)
    op.create_index('ix_hub_event_log_recorded_at', 'hub_event_log', ['recorded_at'], unique=False)
    op.create_table('tenant_deletions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('closed_at', UtcDateTime(), nullable=False),
    sa.Column('deleted_at', UtcDateTime(), nullable=True),
    sa.Column('deleted_by', sa.String(), nullable=False),
    sa.Column('rows_removed', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', name='uq_tenant_deletions_tenant_id')
    )
    op.create_table('tenants',
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('tenant_id')
    )
    op.create_table('trace_cursor',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('position_at', UtcDateTime(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.String(), nullable=False),
    sa.Column('span_count', sa.Integer(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trace_cursor_recorded_at_id', 'trace_cursor', ['recorded_at', 'id'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('username', sa.String(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=False),
    sa.Column('email', sa.String(), nullable=True),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_index('uq_users_email', 'users', ['email'], unique=True, sqlite_where=sa.text('email IS NOT NULL'), postgresql_where=sa.text('email IS NOT NULL'))
    op.create_table('forge_annotation_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('work_source_id', sa.String(), nullable=False),
    sa.Column('annotating', sa.Boolean(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_forge_annotation_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_forge_annotation_facts_tenant_work_source_id_id', 'forge_annotation_facts', ['tenant_id', 'work_source_id', 'id'], unique=False)
    op.create_table('graphs',
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('entry_node_id', sa.String(), nullable=False),
    sa.Column('definition_yaml', sa.Text(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_graphs_tenant_id'),
    sa.PrimaryKeyConstraint('graph_id')
    )
    op.create_index('ix_graphs_tenant_name', 'graphs', ['tenant_id', 'name'], unique=False)
    op.create_table('host_external_usage',
    sa.Column('host_id', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('sampled_at', UtcDateTime(), nullable=False),
    sa.Column('windows', sa.Text(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_host_external_usage_tenant_id'),
    sa.PrimaryKeyConstraint('host_id', 'slug', name='pk_host_external_usage')
    )
    op.create_index('ix_host_external_usage_tenant_id', 'host_external_usage', ['tenant_id'], unique=False)
    op.create_table('host_external_usage_misses',
    sa.Column('host_id', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('missed_at', UtcDateTime(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_host_external_usage_misses_tenant_id'),
    sa.PrimaryKeyConstraint('host_id', 'slug', name='pk_host_external_usage_misses')
    )
    op.create_index('ix_host_external_usage_misses_tenant_id', 'host_external_usage_misses', ['tenant_id'], unique=False)
    op.create_table('host_high_water',
    sa.Column('host_id', sa.String(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_host_high_water_tenant_id'),
    sa.PrimaryKeyConstraint('host_id', name='pk_host_high_water')
    )
    op.create_index('ix_host_high_water_tenant_id', 'host_high_water', ['tenant_id'], unique=False)
    op.create_table('hosts',
    sa.Column('host_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('added_at', UtcDateTime(), nullable=False),
    sa.Column('added_by', sa.String(), nullable=True),
    sa.Column('registered_at', UtcDateTime(), nullable=True),
    sa.Column('last_seen_at', UtcDateTime(), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('redirect_uris', sa.Text(), nullable=True),
    sa.Column('workspaces', sa.Text(), nullable=True),
    sa.Column('subscriptions', sa.Text(), nullable=True),
    sa.Column('token_hash', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_hosts_tenant_id'),
    sa.PrimaryKeyConstraint('host_id', name='pk_hosts')
    )
    op.create_index('ix_hosts_tenant_id', 'hosts', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_hosts_token_hash'), 'hosts', ['token_hash'], unique=False)
    op.create_table('hub_admin_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('granted', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_hub_admin_facts_user_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_hub_admin_facts_user_id', 'hub_admin_facts', ['user_id'], unique=False)
    op.create_table('identities',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('provider_name', sa.String(), nullable=False),
    sa.Column('subject', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('handle', sa.String(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider_name', 'subject', name='uq_identities_provider_subject')
    )
    op.create_index(op.f('ix_identities_user_id'), 'identities', ['user_id'], unique=False)
    op.create_table('invitations',
    sa.Column('invitation_id', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('token_hash', sa.String(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('expires_at', UtcDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_invitations_tenant_id'),
    sa.PrimaryKeyConstraint('invitation_id')
    )
    op.create_index('ix_invitations_tenant_id', 'invitations', ['tenant_id'], unique=False)
    op.create_index('ix_invitations_token_hash', 'invitations', ['token_hash'], unique=False)
    op.create_table('keyed_locks',
    sa.Column('namespace', sa.String(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_keyed_locks_tenant_id'),
    sa.PrimaryKeyConstraint('tenant_id', 'namespace', 'key', name='pk_keyed_locks')
    )
    op.create_table('membership_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=True),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_membership_facts_tenant_id'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_membership_facts_user_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_membership_facts_tenant_user', 'membership_facts', ['tenant_id', 'user_id', 'id'], unique=False)
    op.create_index('ix_membership_facts_user_id', 'membership_facts', ['user_id'], unique=False)
    op.create_table('projects',
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('color', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_projects_tenant_id'),
    sa.PrimaryKeyConstraint('project_id', name='pk_projects'),
    sa.UniqueConstraint('tenant_id', 'slug', name='uq_projects_tenant_slug')
    )
    op.create_table('runner_external_usage',
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('sampled_at', UtcDateTime(), nullable=False),
    sa.Column('windows', sa.Text(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_runner_external_usage_tenant_id'),
    sa.PrimaryKeyConstraint('runner_id', 'slug', name='pk_runner_external_usage')
    )
    op.create_index('ix_runner_external_usage_tenant_id', 'runner_external_usage', ['tenant_id'], unique=False)
    op.create_table('runner_external_usage_misses',
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('missed_at', UtcDateTime(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_runner_external_usage_misses_tenant_id'),
    sa.PrimaryKeyConstraint('runner_id', 'slug')
    )
    op.create_index('ix_runner_external_usage_misses_tenant_id', 'runner_external_usage_misses', ['tenant_id'], unique=False)
    op.create_table('runner_high_water',
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_runner_high_water_tenant_id'),
    sa.PrimaryKeyConstraint('runner_id')
    )
    op.create_index('ix_runner_high_water_tenant_id', 'runner_high_water', ['tenant_id'], unique=False)
    op.create_table('runner_local_pause_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('paused', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_runner_local_pause_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_runner_local_pause_facts_tenant_id', 'runner_local_pause_facts', ['tenant_id'], unique=False)
    op.create_table('secrets',
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('ciphertext', sa.Text(), nullable=False),
    sa.Column('nonce', sa.Text(), nullable=False),
    sa.Column('key_id', sa.String(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('replaced_at', UtcDateTime(), nullable=False),
    sa.Column('replaced_by', sa.String(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_secrets_tenant_id'),
    sa.PrimaryKeyConstraint('tenant_id', 'name', name='pk_secrets')
    )
    op.create_table('sessions',
    sa.Column('id_hash', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('expires_at', UtcDateTime(), nullable=False),
    sa.Column('last_seen_at', UtcDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id_hash')
    )
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('superuser_bootstrap',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('claimed_user_id', sa.String(), nullable=True),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['claimed_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('transcript_event_derivations',
    sa.Column('segment_id', sa.String(), nullable=False),
    sa.Column('extractor_version', sa.String(), nullable=False),
    sa.Column('content_fingerprint', sa.String(), nullable=False),
    sa.Column('derived_at', UtcDateTime(), nullable=False),
    sa.Column('event_count', sa.Integer(), nullable=False),
    sa.Column('complete', sa.Boolean(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_transcript_event_derivations_tenant_id'),
    sa.PrimaryKeyConstraint('tenant_id', 'segment_id', 'extractor_version', name='pk_transcript_event_derivations')
    )
    op.create_index('ix_transcript_event_derivations_egress_order', 'transcript_event_derivations', ['derived_at', 'segment_id', 'extractor_version', 'tenant_id'], unique=False)
    op.create_index('ix_transcript_event_derivations_tenant_derived_at', 'transcript_event_derivations', ['tenant_id', 'derived_at', 'segment_id', 'extractor_version'], unique=False)
    op.create_table('transcript_event_drops',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('segment_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('spawn_generation', sa.Integer(), nullable=False),
    sa.Column('dropped_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_transcript_event_drops_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_transcript_event_drops_egress_order', 'transcript_event_drops', ['dropped_at', 'segment_id', 'tenant_id'], unique=False)
    op.create_index('ix_transcript_event_drops_tenant_chunk_id_epoch', 'transcript_event_drops', ['tenant_id', 'chunk_id', 'epoch'], unique=False)
    op.create_index('ix_transcript_event_drops_tenant_dropped_at_segment_id', 'transcript_event_drops', ['tenant_id', 'dropped_at', 'segment_id'], unique=False)
    op.create_table('transcript_high_water',
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_transcript_high_water_tenant_id'),
    sa.PrimaryKeyConstraint('runner_id')
    )
    op.create_index('ix_transcript_high_water_tenant_id', 'transcript_high_water', ['tenant_id'], unique=False)
    op.create_table('work_item_sequence',
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('next_ref', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_work_item_sequence_tenant_id'),
    sa.PrimaryKeyConstraint('tenant_id', 'source', name='pk_work_item_sequence')
    )
    op.create_table('chunks',
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('minted_at', UtcDateTime(), nullable=False),
    sa.Column('model', sa.String(), server_default='claude-opus-4-8', nullable=False),
    sa.Column('default_model', sa.Text(), nullable=True),
    sa.Column('default_effort', sa.String(), nullable=True),
    sa.Column('default_harnesses', sa.Text(), nullable=True),
    sa.Column('intended_migration', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['graph_id'], ['graphs.graph_id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_chunks_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunks_tenant_id'),
    sa.PrimaryKeyConstraint('chunk_id')
    )
    op.create_index('ix_chunks_tenant_minted_at_chunk_id', 'chunks', ['tenant_id', 'minted_at', 'chunk_id'], unique=False)
    op.create_index('ix_chunks_tenant_project_minted_at_chunk_id', 'chunks', ['tenant_id', 'project_id', 'minted_at', 'chunk_id'], unique=False)
    op.create_table('config_changes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('door', sa.String(), nullable=False),
    sa.Column('record_kind', sa.String(), nullable=False),
    sa.Column('record_key', sa.String(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('op', sa.String(), nullable=False),
    sa.Column('diff', sa.Text(), nullable=False),
    sa.Column('apply_id', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_config_changes_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_config_changes_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_config_changes_tenant_project_id', 'config_changes', ['tenant_id', 'project_id', 'id'], unique=False)
    op.create_index('ix_config_changes_tenant_record', 'config_changes', ['tenant_id', 'record_kind', 'record_key', 'id'], unique=False)
    op.create_table('garden_proposals',
    sa.Column('proposal_id', sa.String(), nullable=False),
    sa.Column('origin', sa.String(), server_default='routine-run', nullable=False),
    sa.Column('routine_name', sa.String(), nullable=True),
    sa.Column('created_by', sa.String(), nullable=True),
    sa.Column('class', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('source_artifact_id', sa.String(), nullable=True),
    sa.Column('ref', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.CheckConstraint("origin != 'operator' OR created_by IS NOT NULL", name='ck_garden_proposals_operator_has_created_by'),
    sa.CheckConstraint("origin != 'routine-run' OR routine_name IS NOT NULL", name='ck_garden_proposals_routine_run_has_routine'),
    sa.CheckConstraint("origin IN ('routine-run', 'operator')", name='ck_garden_proposals_origin'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_garden_proposals_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_garden_proposals_tenant_id'),
    sa.PrimaryKeyConstraint('proposal_id')
    )
    op.create_index('ix_garden_proposals_tenant_project_routine_class', 'garden_proposals', ['tenant_id', 'project_id', 'routine_name', 'class'], unique=False)
    op.create_index('ux_garden_proposals_source_artifact_ref', 'garden_proposals', ['source_artifact_id', 'ref'], unique=True)
    op.create_table('graph_artifacts',
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['graph_id'], ['graphs.graph_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_graph_artifacts_tenant_id'),
    sa.PrimaryKeyConstraint('graph_id', 'name')
    )
    op.create_index('ix_graph_artifacts_tenant_graph_id', 'graph_artifacts', ['tenant_id', 'graph_id'], unique=False)
    op.create_table('graph_lifecycle_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('retired', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['graph_id'], ['graphs.graph_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_graph_lifecycle_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_graph_lifecycle_facts_tenant_graph_id', 'graph_lifecycle_facts', ['tenant_id', 'graph_id'], unique=False)
    op.create_table('graph_nodes',
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('executor', sa.String(), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=True),
    sa.Column('judgement_prompt', sa.Text(), nullable=True),
    sa.Column('session', sa.String(), nullable=False),
    sa.Column('session_source', sa.String(), nullable=True),
    sa.Column('judged_by', sa.String(), nullable=False),
    sa.Column('retries_max', sa.Integer(), nullable=True),
    sa.Column('retries_exhausted', sa.String(), nullable=True),
    sa.Column('produces', sa.Text(), nullable=True),
    sa.Column('checks', sa.Text(), nullable=True),
    sa.Column('checks_cwd', sa.String(), nullable=True),
    sa.Column('checks_timeout', sa.Integer(), nullable=True),
    sa.Column('bounce_cap', sa.Integer(), nullable=True),
    sa.Column('run', sa.Text(), nullable=True),
    sa.Column('poll_interval_seconds', sa.Integer(), nullable=True),
    sa.Column('poll_timeout_seconds', sa.Integer(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['graph_id'], ['graphs.graph_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_graph_nodes_tenant_id'),
    sa.PrimaryKeyConstraint('node_id')
    )
    op.create_index('ix_graph_nodes_tenant_graph_id', 'graph_nodes', ['tenant_id', 'graph_id'], unique=False)
    op.create_table('graph_policy_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('follow_latest', sa.Boolean(), nullable=True),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['graph_id'], ['graphs.graph_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_graph_policy_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_graph_policy_facts_tenant_id', 'graph_policy_facts', ['tenant_id'], unique=False)
    op.create_table('graph_sessions',
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('model', sa.Text(), nullable=True),
    sa.Column('effort', sa.String(), nullable=True),
    sa.Column('rotate_max_context_tokens', sa.Integer(), nullable=True),
    sa.Column('rotate_max_transcript_bytes', sa.Integer(), nullable=True),
    sa.Column('rotate_max_invocations', sa.Integer(), nullable=True),
    sa.Column('compaction_window', sa.String(), nullable=True),
    sa.Column('harnesses', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['graph_id'], ['graphs.graph_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_graph_sessions_tenant_id'),
    sa.PrimaryKeyConstraint('graph_id', 'name')
    )
    op.create_index('ix_graph_sessions_tenant_graph_id', 'graph_sessions', ['tenant_id', 'graph_id'], unique=False)
    op.create_table('host_lifecycle_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('host_id', sa.String(), nullable=False),
    sa.Column('retired', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.host_id'], name='fk_host_lifecycle_facts_hosts'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_host_lifecycle_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_host_lifecycle_facts_tenant_id', 'host_lifecycle_facts', ['tenant_id'], unique=False)
    op.create_table('host_token_revocations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('host_id', sa.String(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('revoked_at', UtcDateTime(), nullable=False),
    sa.Column('revoked_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.host_id'], name='fk_host_token_revocations_hosts'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_host_token_revocations_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_host_token_revocations_tenant_id', 'host_token_revocations', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_host_token_revocations_token_hash'), 'host_token_revocations', ['token_hash'], unique=False)
    op.create_table('invitation_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('invitation_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('actor_user_id', sa.String(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.CheckConstraint("kind IN ('accepted', 'revoked')", name='ck_invitation_facts_kind'),
    sa.ForeignKeyConstraint(['invitation_id'], ['invitations.invitation_id'], name='fk_invitation_facts_invitation_id'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_invitation_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('invitation_id', name='uq_invitation_facts_invitation_id')
    )
    op.create_index('ix_invitation_facts_tenant_id', 'invitation_facts', ['tenant_id'], unique=False)
    op.create_table('project_former_slugs',
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('left_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_project_former_slugs_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_project_former_slugs_tenant_id'),
    sa.PrimaryKeyConstraint('tenant_id', 'slug', name='pk_project_former_slugs')
    )
    op.create_table('repositories',
    sa.Column('repository_id', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('forge_api_url', sa.String(), nullable=False),
    sa.Column('owner', sa.String(), nullable=False),
    sa.Column('repo', sa.String(), nullable=False),
    sa.Column('base_branch', sa.String(), nullable=False),
    sa.Column('secret_name', sa.String(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'secret_name'], ['secrets.tenant_id', 'secrets.name'], name='fk_repositories_secrets'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_repositories_tenant_id'),
    sa.PrimaryKeyConstraint('repository_id', name='pk_repositories'),
    sa.UniqueConstraint('tenant_id', 'forge_api_url', 'owner', 'repo', name='uq_repositories_tenant_coordinate'),
    sa.UniqueConstraint('tenant_id', 'slug', name='uq_repositories_tenant_slug')
    )
    op.create_table('runner_registrations',
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('added_at', UtcDateTime(), nullable=False),
    sa.Column('added_by', sa.String(), nullable=True),
    sa.Column('workspace_id', sa.String(), nullable=True),
    sa.Column('registered_at', UtcDateTime(), nullable=True),
    sa.Column('last_seen_at', UtcDateTime(), nullable=True),
    sa.Column('token_hash', sa.Text(), nullable=True),
    sa.Column('env_capacity', sa.Integer(), nullable=True),
    sa.Column('public_url', sa.Text(), nullable=True),
    sa.Column('redirect_uris', sa.Text(), nullable=True),
    sa.Column('capabilities', sa.Text(), nullable=True),
    sa.Column('subscriptions', sa.Text(), nullable=True),
    sa.Column('gates', sa.Text(), nullable=True),
    sa.Column('projects', sa.Text(), nullable=True),
    sa.Column('host_id', sa.String(), nullable=True),
    sa.Column('bindings', sa.Text(), nullable=True),
    sa.Column('subscription', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.host_id'], name='fk_runner_registrations_hosts'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_runner_registrations_tenant_id'),
    sa.PrimaryKeyConstraint('runner_id')
    )
    op.create_index('ix_runner_registrations_tenant_host_id', 'runner_registrations', ['tenant_id', 'host_id'], unique=False)
    op.create_index('ix_runner_registrations_tenant_id', 'runner_registrations', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_runner_registrations_token_hash'), 'runner_registrations', ['token_hash'], unique=False)
    op.create_table('scopes',
    sa.Column('scope_id', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_scopes_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_scopes_tenant_id'),
    sa.PrimaryKeyConstraint('scope_id', name='pk_scopes'),
    sa.UniqueConstraint('project_id', 'slug', name='uq_scopes_project_slug')
    )
    op.create_index('ix_scopes_tenant_slug', 'scopes', ['tenant_id', 'slug'], unique=False)
    op.create_table('secret_lifecycle_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('retired', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'name'], ['secrets.tenant_id', 'secrets.name'], name='fk_secret_lifecycle_facts_secrets'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_secret_lifecycle_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_secret_lifecycle_facts_tenant_id', 'secret_lifecycle_facts', ['tenant_id'], unique=False)
    op.create_table('work_items',
    sa.Column('work_item_id', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('ref', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('author_kind', sa.String(), nullable=False),
    sa.Column('author_payload', sa.Text(), nullable=False),
    sa.Column('stated_priority', sa.String(), nullable=True),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('edited_at', UtcDateTime(), nullable=False),
    sa.Column('closed_at', UtcDateTime(), nullable=True),
    sa.Column('closure', sa.String(), nullable=True),
    sa.Column('routine_name', sa.String(), nullable=True),
    sa.Column('scope_slug', sa.String(), nullable=True),
    sa.Column('run_mode', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_work_items_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_work_items_tenant_id'),
    sa.PrimaryKeyConstraint('work_item_id'),
    sa.UniqueConstraint('tenant_id', 'source', 'ref', name='uq_work_items_tenant_source_ref')
    )
    op.create_index('ix_work_items_tenant_source', 'work_items', ['tenant_id', 'source'], unique=False)
    op.create_table('work_sources',
    sa.Column('work_source_id', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('locator', sa.String(), nullable=False),
    sa.Column('api_base', sa.String(), nullable=True),
    sa.Column('web_base', sa.String(), nullable=True),
    sa.Column('annotate', sa.Boolean(), nullable=False),
    sa.Column('secret_name', sa.String(), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'secret_name'], ['secrets.tenant_id', 'secrets.name'], name='fk_work_sources_secrets'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_work_sources_tenant_id'),
    sa.PrimaryKeyConstraint('work_source_id', name='pk_work_sources'),
    sa.UniqueConstraint('tenant_id', 'provider', 'locator', name='uq_work_sources_tenant_provider_locator'),
    sa.UniqueConstraint('tenant_id', 'slug', name='uq_work_sources_tenant_slug')
    )
    op.create_table('artifacts',
    sa.Column('artifact_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('node_name', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('data', sa.Text(), nullable=False),
    sa.Column('repo', sa.String(), nullable=True),
    sa.Column('forge', sa.String(), nullable=True),
    sa.Column('produced_at', UtcDateTime(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_artifacts_tenant_id'),
    sa.PrimaryKeyConstraint('artifact_id')
    )
    op.create_index('ix_artifacts_tenant_chunk_id_node_id_epoch', 'artifacts', ['tenant_id', 'chunk_id', 'node_id', 'epoch'], unique=False)
    op.create_table('chunk_bounces',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('cause', sa.String(), nullable=False),
    sa.Column('envelope', sa.Text(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_bounces_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_bounces_tenant_chunk_id_epoch', 'chunk_bounces', ['tenant_id', 'chunk_id', 'epoch'], unique=False)
    op.create_table('chunk_completed',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('completed_at', UtcDateTime(), nullable=False),
    sa.Column('completed_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_completed_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_completed_completed_at_id', 'chunk_completed', ['completed_at', 'id'], unique=False)
    op.create_index('ix_chunk_completed_tenant_chunk_id', 'chunk_completed', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_chunk_completed_tenant_completed_at_id', 'chunk_completed', ['tenant_id', 'completed_at', 'id'], unique=False)
    op.create_table('chunk_deleted',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('deleted_at', UtcDateTime(), nullable=False),
    sa.Column('deleted_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_deleted_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_deleted_tenant_deleted_at_id', 'chunk_deleted', ['tenant_id', 'deleted_at', 'id'], unique=False)
    op.create_table('chunk_demoted',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('demoted_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_demoted_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_demoted_tenant_chunk_id', 'chunk_demoted', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_chunk_demoted_tenant_demoted_at_id', 'chunk_demoted', ['tenant_id', 'demoted_at', 'id'], unique=False)
    op.create_table('chunk_dependencies',
    sa.Column('dependency_id', sa.String(), nullable=False),
    sa.Column('dependent_chunk_id', sa.String(), nullable=False),
    sa.Column('prerequisite_chunk_id', sa.String(), nullable=False),
    sa.Column('declared_at', UtcDateTime(), nullable=False),
    sa.Column('declared_by', sa.String(), nullable=False),
    sa.Column('released_at', UtcDateTime(), nullable=True),
    sa.Column('released_by', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['dependent_chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['prerequisite_chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_dependencies_tenant_id'),
    sa.PrimaryKeyConstraint('dependency_id')
    )
    op.create_index('ix_chunk_dependencies_tenant_dependent_chunk_id', 'chunk_dependencies', ['tenant_id', 'dependent_chunk_id'], unique=False)
    op.create_index('ix_chunk_dependencies_tenant_prerequisite_chunk_id', 'chunk_dependencies', ['tenant_id', 'prerequisite_chunk_id'], unique=False)
    op.create_table('chunk_grouped',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('grouped_into', sa.String(), nullable=False),
    sa.Column('grouped_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['grouped_into'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_grouped_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_grouped_tenant_grouped_at_id', 'chunk_grouped', ['tenant_id', 'grouped_at', 'id'], unique=False)
    op.create_table('chunk_migrations',
    sa.Column('migration_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('from_node_id', sa.String(), nullable=True),
    sa.Column('from_graph_id', sa.String(), nullable=False),
    sa.Column('to_graph_id', sa.String(), nullable=False),
    sa.Column('landed_node_id', sa.String(), nullable=True),
    sa.Column('choice_name', sa.String(), nullable=True),
    sa.Column('decision_id', sa.String(), nullable=True),
    sa.Column('model_after', sa.String(), nullable=True),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('source', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_migrations_tenant_id'),
    sa.PrimaryKeyConstraint('migration_id')
    )
    op.create_index('ix_chunk_migrations_recorded_at_migration_id', 'chunk_migrations', ['recorded_at', 'migration_id'], unique=False)
    op.create_index('ix_chunk_migrations_tenant_chunk_id', 'chunk_migrations', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_chunk_migrations_tenant_chunk_id_recorded_at', 'chunk_migrations', ['tenant_id', 'chunk_id', 'recorded_at'], unique=False)
    op.create_index('ix_chunk_migrations_tenant_recorded_at_migration_id', 'chunk_migrations', ['tenant_id', 'recorded_at', 'migration_id'], unique=False)
    op.create_table('chunk_pause_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('paused', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_pause_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_pause_facts_tenant_chunk_id', 'chunk_pause_facts', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_chunk_pause_facts_tenant_set_at_id', 'chunk_pause_facts', ['tenant_id', 'set_at', 'id'], unique=False)
    op.create_table('chunk_promoted',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('promoted_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_promoted_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_promoted_tenant_chunk_id', 'chunk_promoted', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_chunk_promoted_tenant_promoted_at_id', 'chunk_promoted', ['tenant_id', 'promoted_at', 'id'], unique=False)
    op.create_table('chunk_restarts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('from_node_id', sa.String(), nullable=True),
    sa.Column('from_graph_id', sa.String(), nullable=True),
    sa.Column('to_node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.String(), nullable=True),
    sa.Column('restarted_by', sa.String(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_restarts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_restarts_recorded_at_id', 'chunk_restarts', ['recorded_at', 'id'], unique=False)
    op.create_index('ix_chunk_restarts_tenant_chunk_id', 'chunk_restarts', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_chunk_restarts_tenant_chunk_id_recorded_at', 'chunk_restarts', ['tenant_id', 'chunk_id', 'recorded_at'], unique=False)
    op.create_index('ix_chunk_restarts_tenant_recorded_at_id', 'chunk_restarts', ['tenant_id', 'recorded_at', 'id'], unique=False)
    op.create_table('chunk_stopped',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('stopped_at', UtcDateTime(), nullable=False),
    sa.Column('stopped_by', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_stopped_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_stopped_stopped_at_id', 'chunk_stopped', ['stopped_at', 'id'], unique=False)
    op.create_index('ix_chunk_stopped_tenant_chunk_id', 'chunk_stopped', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_chunk_stopped_tenant_stopped_at_id', 'chunk_stopped', ['tenant_id', 'stopped_at', 'id'], unique=False)
    op.create_table('chunk_work_refs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('ref', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_chunk_work_refs_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunk_work_refs_tenant_chunk_id', 'chunk_work_refs', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_chunk_work_refs_tenant_source_ref', 'chunk_work_refs', ['tenant_id', 'source', 'ref'], unique=False)
    op.create_table('close_intents',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('ref', sa.String(), nullable=False),
    sa.Column('enqueued_at', UtcDateTime(), nullable=False),
    sa.Column('retired_at', UtcDateTime(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_close_intents_tenant_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('chunk_id', 'source', 'ref', name='uq_close_intents_chunk_source_ref')
    )
    op.create_index('ix_close_intents_pending', 'close_intents', ['retired_at'], unique=False)
    op.create_index('ix_close_intents_tenant_id', 'close_intents', ['tenant_id'], unique=False)
    op.create_table('decisions',
    sa.Column('decision_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('node_name', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('choices', sa.Text(), nullable=False),
    sa.Column('check_results', sa.Text(), server_default='[]', nullable=False),
    sa.Column('submitted_at', UtcDateTime(), nullable=False),
    sa.Column('imposed_by_runner_id', sa.String(), nullable=True),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_decisions_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_decisions_tenant_id'),
    sa.PrimaryKeyConstraint('decision_id')
    )
    op.create_index('ix_decisions_submitted_at_decision_id', 'decisions', ['submitted_at', 'decision_id'], unique=False)
    op.create_index('ix_decisions_tenant_chunk_id', 'decisions', ['tenant_id', 'chunk_id', 'submitted_at', 'decision_id'], unique=False)
    op.create_index('ix_decisions_tenant_project_submitted_at_decision_id', 'decisions', ['tenant_id', 'project_id', 'submitted_at', 'decision_id'], unique=False)
    op.create_index('ix_decisions_tenant_submitted_at_decision_id', 'decisions', ['tenant_id', 'submitted_at', 'decision_id'], unique=False)
    op.create_table('delivery_landed',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('landed_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_delivery_landed_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_delivery_landed_tenant_chunk_id', 'delivery_landed', ['tenant_id', 'chunk_id'], unique=False)
    op.create_table('delivery_repo_landed',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('repo', sa.String(), nullable=False),
    sa.Column('commit_hash', sa.String(), nullable=False),
    sa.Column('landed_at', UtcDateTime(), nullable=False),
    sa.Column('repository_id', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_delivery_repo_landed_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_delivery_repo_landed_tenant_chunk_id', 'delivery_repo_landed', ['tenant_id', 'chunk_id'], unique=False)
    op.create_table('epoch_owners',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=True),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_epoch_owners_tenant_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('chunk_id', 'epoch', name='uq_epoch_owners_chunk_id_epoch')
    )
    op.create_index('ix_epoch_owners_recorded_at_id', 'epoch_owners', ['recorded_at', 'id'], unique=False)
    op.create_index('ix_epoch_owners_tenant_chunk_id', 'epoch_owners', ['tenant_id', 'chunk_id', 'epoch'], unique=False)
    op.create_index('ix_epoch_owners_tenant_recorded_at_id', 'epoch_owners', ['tenant_id', 'recorded_at', 'id'], unique=False)
    op.create_table('escalations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('takeover_command', sa.Text(), server_default='', nullable=False),
    sa.Column('wrapped_takeover_command', sa.Text(), server_default='', nullable=False),
    sa.Column('decision_id', sa.String(), nullable=True),
    sa.Column('cause', sa.Text(), nullable=True),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_escalations_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_escalations_recorded_at_id', 'escalations', ['recorded_at', 'id'], unique=False)
    op.create_index('ix_escalations_tenant_chunk_id', 'escalations', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_escalations_tenant_recorded_at_id', 'escalations', ['tenant_id', 'recorded_at', 'id'], unique=False)
    op.create_table('event_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('severity', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=True),
    sa.Column('chunk_id', sa.String(), nullable=True),
    sa.Column('lease_id', sa.String(), nullable=True),
    sa.Column('node_name', sa.String(), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_event_log_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_event_log_tenant_recorded_at', 'event_log', ['tenant_id', 'recorded_at'], unique=False)
    op.create_table('findings',
    sa.Column('finding_id', sa.String(), nullable=False),
    sa.Column('routine_name', sa.String(), nullable=True),
    sa.Column('scope_id', sa.String(), nullable=False),
    sa.Column('scope_slug', sa.String(), nullable=False),
    sa.Column('class', sa.String(), nullable=False),
    sa.Column('locus', sa.String(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('introduced', sa.String(), nullable=True),
    sa.Column('introduced_at', UtcDateTime(), nullable=True),
    sa.Column('source', sa.String(), server_default='routine', nullable=False),
    sa.Column('severity', sa.String(), nullable=True),
    sa.Column('raised_by_chunk_id', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.CheckConstraint("source IN ('routine', 'review')", name='ck_findings_source'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_findings_projects'),
    sa.ForeignKeyConstraint(['raised_by_chunk_id'], ['chunks.chunk_id'], name='fk_findings_raised_by_chunk_id'),
    sa.ForeignKeyConstraint(['scope_id'], ['scopes.scope_id'], name='fk_findings_scopes'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_findings_tenant_id'),
    sa.PrimaryKeyConstraint('finding_id')
    )
    op.create_index('ix_findings_tenant_project_routine_class', 'findings', ['tenant_id', 'project_id', 'routine_name', 'class'], unique=False)
    op.create_index('ix_findings_tenant_project_routine_scope', 'findings', ['tenant_id', 'project_id', 'routine_name', 'scope_slug'], unique=False)
    op.create_index('ix_findings_tenant_project_scope_source', 'findings', ['tenant_id', 'project_id', 'scope_slug', 'source'], unique=False)
    op.create_table('garden_proposal_closures',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('proposal_id', sa.String(), nullable=False),
    sa.Column('closure', sa.String(), nullable=False),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('closed_by', sa.String(), nullable=False),
    sa.Column('closed_at', UtcDateTime(), nullable=False),
    sa.Column('item_outcome', sa.String(), nullable=True),
    sa.Column('source', sa.String(), nullable=True),
    sa.Column('ref', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['proposal_id'], ['garden_proposals.proposal_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_garden_proposal_closures_tenant_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('proposal_id', name='uq_garden_proposal_closures_proposal_id')
    )
    op.create_index('ix_garden_proposal_closures_tenant_source_ref', 'garden_proposal_closures', ['tenant_id', 'source', 'ref'], unique=True)
    op.create_table('graph_choices',
    sa.Column('choice_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('requires_checks', sa.Boolean(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['node_id'], ['graph_nodes.node_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_graph_choices_tenant_id'),
    sa.PrimaryKeyConstraint('choice_id')
    )
    op.create_index('ix_graph_choices_tenant_node_id', 'graph_choices', ['tenant_id', 'node_id'], unique=False)
    op.create_table('hub_exec_slot',
    sa.Column('slot_id', sa.String(), nullable=False),
    sa.Column('holder_chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('acquired_at', UtcDateTime(), nullable=False),
    sa.Column('released_at', UtcDateTime(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['holder_chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_hub_exec_slot_tenant_id'),
    sa.PrimaryKeyConstraint('slot_id')
    )
    op.create_index('ix_hub_exec_slot_tenant_id', 'hub_exec_slot', ['tenant_id'], unique=False)
    op.create_table('hub_node_poll',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('polled_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_hub_node_poll_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_hub_node_poll_tenant_chunk_id', 'hub_node_poll', ['tenant_id', 'chunk_id'], unique=False)
    op.create_table('lease_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('minted_at', UtcDateTime(), nullable=False),
    sa.Column('lease_id', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_lease_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_lease_facts_minted_at', 'lease_facts', ['minted_at'], unique=False)
    op.create_index('ix_lease_facts_tenant_chunk_id_epoch', 'lease_facts', ['tenant_id', 'chunk_id', 'epoch'], unique=False)
    op.create_index('ix_lease_facts_tenant_minted_at', 'lease_facts', ['tenant_id', 'minted_at'], unique=False)
    op.create_table('project_repository_links',
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('repository_id', sa.String(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_project_repository_links_projects'),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.repository_id'], name='fk_project_repository_links_repositories'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_project_repository_links_tenant_id'),
    sa.PrimaryKeyConstraint('project_id', 'repository_id', name='pk_project_repository_links')
    )
    op.create_index('ix_project_repository_links_tenant_repository', 'project_repository_links', ['tenant_id', 'repository_id'], unique=False)
    op.create_table('project_source_links',
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('work_source_id', sa.String(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_project_source_links_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_project_source_links_tenant_id'),
    sa.ForeignKeyConstraint(['work_source_id'], ['work_sources.work_source_id'], name='fk_project_source_links_work_sources'),
    sa.PrimaryKeyConstraint('project_id', 'work_source_id', name='pk_project_source_links')
    )
    op.create_index('ix_project_source_links_tenant_source', 'project_source_links', ['tenant_id', 'work_source_id'], unique=False)
    op.create_table('questions',
    sa.Column('question_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=True),
    sa.Column('session_id', sa.String(), nullable=True),
    sa.Column('harness_id', sa.String(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('options', sa.Text(), nullable=False),
    sa.Column('asked_at', UtcDateTime(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_questions_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_questions_tenant_id'),
    sa.PrimaryKeyConstraint('tenant_id', 'question_id', name='pk_questions')
    )
    op.create_index('ix_questions_tenant_asked_at_question_id', 'questions', ['tenant_id', 'asked_at', 'question_id'], unique=False)
    op.create_index('ix_questions_tenant_chunk_id', 'questions', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_questions_tenant_project_asked_at_question_id', 'questions', ['tenant_id', 'project_id', 'asked_at', 'question_id'], unique=False)
    op.create_table('queue_positions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('position', sa.Float(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_queue_positions_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_queue_positions_tenant_chunk_id', 'queue_positions', ['tenant_id', 'chunk_id'], unique=False)
    op.create_table('repository_former_slugs',
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('repository_id', sa.String(), nullable=False),
    sa.Column('left_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.repository_id'], name='fk_repository_former_slugs_repositories'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_repository_former_slugs_tenant_id'),
    sa.PrimaryKeyConstraint('tenant_id', 'slug', name='pk_repository_former_slugs')
    )
    op.create_table('repository_lifecycle_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('repository_id', sa.String(), nullable=False),
    sa.Column('retired', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.repository_id'], name='fk_repository_lifecycle_facts_repositories'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_repository_lifecycle_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_repository_lifecycle_facts_tenant_id', 'repository_lifecycle_facts', ['tenant_id'], unique=False)
    op.create_table('requeues',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('requeued_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_requeues_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_requeues_tenant_chunk_id', 'requeues', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_requeues_tenant_requeued_at_id', 'requeues', ['tenant_id', 'requeued_at', 'id'], unique=False)
    op.create_table('route_created',
    sa.Column('route_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_route_created_tenant_id'),
    sa.PrimaryKeyConstraint('route_id')
    )
    op.create_index('ix_route_created_tenant_chunk_id', 'route_created', ['tenant_id', 'chunk_id', 'created_at', 'seq'], unique=False)
    op.create_index('ix_route_created_tenant_created_at_route_id', 'route_created', ['tenant_id', 'created_at', 'route_id'], unique=False)
    op.create_index('ix_route_created_tenant_runner_id', 'route_created', ['tenant_id', 'runner_id', 'chunk_id'], unique=False)
    op.create_table('route_released',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('released_at', UtcDateTime(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_route_released_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_route_released_released_at_id', 'route_released', ['released_at', 'id'], unique=False)
    op.create_index('ix_route_released_tenant_chunk_id', 'route_released', ['tenant_id', 'chunk_id', 'released_at', 'seq'], unique=False)
    op.create_index('ix_route_released_tenant_released_at_id', 'route_released', ['tenant_id', 'released_at', 'id'], unique=False)
    op.create_table('route_token_minted',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('minted_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_route_token_minted_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_route_token_minted_tenant_chunk_id', 'route_token_minted', ['tenant_id', 'chunk_id'], unique=False)
    op.create_table('routines',
    sa.Column('routine_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('graph_name', sa.String(), nullable=False),
    sa.Column('default_scope_id', sa.String(), nullable=False),
    sa.Column('default_model', sa.Text(), nullable=True),
    sa.Column('default_effort', sa.String(), nullable=True),
    sa.Column('default_harnesses', sa.Text(), nullable=True),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['default_scope_id'], ['scopes.scope_id'], name='fk_routines_scopes'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_routines_projects'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_routines_tenant_id'),
    sa.PrimaryKeyConstraint('routine_id'),
    sa.UniqueConstraint('project_id', 'name', name='uq_routines_project_name')
    )
    op.create_index('ix_routines_tenant_name', 'routines', ['tenant_id', 'name'], unique=False)
    op.create_table('runner_lifecycle_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('retired', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['runner_id'], ['runner_registrations.runner_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_runner_lifecycle_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_runner_lifecycle_facts_tenant_id', 'runner_lifecycle_facts', ['tenant_id'], unique=False)
    op.create_table('runner_pause_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('paused', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['runner_id'], ['runner_registrations.runner_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_runner_pause_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_runner_pause_facts_tenant_id', 'runner_pause_facts', ['tenant_id'], unique=False)
    op.create_table('runner_token_revocations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('revoked_at', UtcDateTime(), nullable=False),
    sa.Column('revoked_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['runner_id'], ['runner_registrations.runner_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_runner_token_revocations_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_runner_token_revocations_tenant_id', 'runner_token_revocations', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_runner_token_revocations_token_hash'), 'runner_token_revocations', ['token_hash'], unique=False)
    op.create_table('scope_lifecycle_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('scope_id', sa.String(), nullable=False),
    sa.Column('retired', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['scope_id'], ['scopes.scope_id'], name='fk_scope_lifecycle_facts_scopes'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_scope_lifecycle_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_scope_lifecycle_facts_tenant_id', 'scope_lifecycle_facts', ['tenant_id'], unique=False)
    op.create_table('transcript_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('segment_id', sa.String(), nullable=False),
    sa.Column('extractor_version', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('turn_path', sa.String(), nullable=False),
    sa.Column('occurrence', sa.Integer(), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('subject', sa.String(), nullable=True),
    sa.Column('tool', sa.String(), nullable=True),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('spawn_generation', sa.Integer(), nullable=False),
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('depth', sa.Integer(), nullable=False),
    sa.Column('agent_type', sa.String(), nullable=True),
    sa.Column('harness_id', sa.String(), nullable=True),
    sa.Column('harness_version', sa.String(), nullable=True),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('effort', sa.String(), nullable=True),
    sa.Column('occurred_at', UtcDateTime(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_transcript_events_tenant_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'segment_id', 'extractor_version', 'kind', 'turn_path', 'occurrence', name='uq_transcript_events_tenant_natural_key')
    )
    op.create_index('ix_transcript_events_extractor_version_id', 'transcript_events', ['extractor_version', 'id'], unique=False)
    op.create_index('ix_transcript_events_tenant_chunk_id', 'transcript_events', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_transcript_events_tenant_subject', 'transcript_events', ['tenant_id', 'subject'], unique=False)
    op.create_index('ix_transcript_events_tenant_tool', 'transcript_events', ['tenant_id', 'tool'], unique=False)
    op.create_table('transcript_segments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('segment_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('spawn_generation', sa.Integer(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('turn_range_start', sa.Integer(), nullable=False),
    sa.Column('turn_range_end', sa.Integer(), nullable=False),
    sa.Column('final', sa.Boolean(), nullable=False),
    sa.Column('rejected', sa.Boolean(), nullable=False),
    sa.Column('rejection_reason', sa.String(), nullable=True),
    sa.Column('byte_count', sa.Integer(), nullable=False),
    sa.Column('codec', sa.String(), nullable=True),
    sa.Column('content', sa.LargeBinary(), nullable=True),
    sa.Column('harness_id', sa.String(), nullable=True),
    sa.Column('normalizer_version', sa.String(), nullable=False),
    sa.Column('harness_version', sa.String(), nullable=True),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('effort', sa.String(), nullable=True),
    sa.Column('spawn_cwd', sa.String(), nullable=True),
    sa.Column('record_truncated', sa.Boolean(), nullable=True),
    sa.Column('supersedes', sa.String(), nullable=True),
    sa.Column('received_at', UtcDateTime(), nullable=False),
    sa.Column('content_digest', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_transcript_segments_tenant_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'segment_id', 'turn_range_start', name='uq_transcript_segments_tenant_segment_turn_start')
    )
    op.create_index('ix_transcript_segments_final_chunk_id', 'transcript_segments', ['final', 'chunk_id'], unique=False)
    op.create_index('ix_transcript_segments_tenant_chunk_id', 'transcript_segments', ['tenant_id', 'chunk_id', 'segment_id', 'turn_range_start'], unique=False)
    op.create_index('ix_transcript_segments_tenant_runner_received_at', 'transcript_segments', ['tenant_id', 'runner_id', 'received_at'], unique=False)
    op.create_index('ix_transcript_segments_tenant_supersedes', 'transcript_segments', ['tenant_id', 'supersedes'], unique=False)
    op.create_table('transitions',
    sa.Column('transition_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('from_node_id', sa.String(), nullable=True),
    sa.Column('to_node_id', sa.String(), nullable=False),
    sa.Column('choice_name', sa.String(), nullable=True),
    sa.Column('decision_id', sa.String(), nullable=True),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_transitions_tenant_id'),
    sa.PrimaryKeyConstraint('transition_id')
    )
    op.create_index('ix_transitions_recorded_at_transition_id', 'transitions', ['recorded_at', 'transition_id'], unique=False)
    op.create_index('ix_transitions_tenant_chunk_id_epoch', 'transitions', ['tenant_id', 'chunk_id', 'epoch'], unique=False)
    op.create_index('ix_transitions_tenant_chunk_id_recorded_at', 'transitions', ['tenant_id', 'chunk_id', 'recorded_at'], unique=False)
    op.create_index('ix_transitions_tenant_recorded_at_transition_id', 'transitions', ['tenant_id', 'recorded_at', 'transition_id'], unique=False)
    op.create_index('ix_transitions_tenant_to_node_id', 'transitions', ['tenant_id', 'to_node_id'], unique=False)
    op.create_table('usage_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('model', sa.String(), nullable=False),
    sa.Column('harness_id', sa.String(), nullable=True),
    sa.Column('harness_version', sa.String(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_create_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('estimated_cost_usd', sa.Float(), nullable=True),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_usage_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_usage_facts_recorded_at_id', 'usage_facts', ['recorded_at', 'id'], unique=False)
    op.create_index('ix_usage_facts_tenant_chunk_id', 'usage_facts', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_usage_facts_tenant_node_id', 'usage_facts', ['tenant_id', 'node_id'], unique=False)
    op.create_index('ix_usage_facts_tenant_recorded_at_id', 'usage_facts', ['tenant_id', 'recorded_at', 'id'], unique=False)
    op.create_table('work_item_closures',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('ref', sa.String(), nullable=False),
    sa.Column('outcome', sa.String(), nullable=False),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_work_item_closures_tenant_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('chunk_id', 'source', 'ref', 'outcome', name='uq_work_item_closures_chunk_source_ref_outcome')
    )
    op.create_index('ix_work_item_closures_tenant_id', 'work_item_closures', ['tenant_id'], unique=False)
    op.create_table('work_item_runs',
    sa.Column('work_item_id', sa.String(), nullable=False),
    sa.Column('routine_name', sa.String(), nullable=False),
    sa.Column('scope_id', sa.String(), nullable=False),
    sa.Column('mode', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_work_item_runs_projects'),
    sa.ForeignKeyConstraint(['scope_id'], ['scopes.scope_id'], name='fk_work_item_runs_scopes'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_work_item_runs_tenant_id'),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.work_item_id'], ),
    sa.PrimaryKeyConstraint('work_item_id')
    )
    op.create_index('ix_work_item_runs_tenant_id', 'work_item_runs', ['tenant_id'], unique=False)
    op.create_table('work_source_former_slugs',
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('work_source_id', sa.String(), nullable=False),
    sa.Column('left_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_work_source_former_slugs_tenant_id'),
    sa.ForeignKeyConstraint(['work_source_id'], ['work_sources.work_source_id'], name='fk_work_source_former_slugs_work_sources'),
    sa.PrimaryKeyConstraint('tenant_id', 'slug', name='pk_work_source_former_slugs')
    )
    op.create_table('work_source_lifecycle_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('work_source_id', sa.String(), nullable=False),
    sa.Column('retired', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_work_source_lifecycle_facts_tenant_id'),
    sa.ForeignKeyConstraint(['work_source_id'], ['work_sources.work_source_id'], name='fk_work_source_lifecycle_facts_work_sources'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_work_source_lifecycle_facts_tenant_id', 'work_source_lifecycle_facts', ['tenant_id'], unique=False)
    op.create_table('answer_deliveries',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('question_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('delivered_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['tenant_id', 'question_id'], ['questions.tenant_id', 'questions.question_id'], name='fk_answer_deliveries_questions'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_answer_deliveries_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_answer_deliveries_tenant_question_id', 'answer_deliveries', ['tenant_id', 'question_id'], unique=False)
    op.create_table('close_intent_attempts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('intent_id', sa.Integer(), nullable=False),
    sa.Column('attempted_at', UtcDateTime(), nullable=False),
    sa.Column('outcome', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['intent_id'], ['close_intents.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_close_intent_attempts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_close_intent_attempts_tenant_intent_id', 'close_intent_attempts', ['tenant_id', 'intent_id'], unique=False)
    op.create_table('decision_resolutions',
    sa.Column('decision_id', sa.String(), nullable=False),
    sa.Column('choice', sa.String(), nullable=False),
    sa.Column('resolved_by', sa.String(), nullable=False),
    sa.Column('resolved_at', UtcDateTime(), nullable=False),
    sa.Column('overrode_red_checks', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['decision_id'], ['decisions.decision_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_decision_resolutions_tenant_id'),
    sa.PrimaryKeyConstraint('decision_id')
    )
    op.create_index('ix_decision_resolutions_tenant_resolved_at_decision_id', 'decision_resolutions', ['tenant_id', 'resolved_at', 'decision_id'], unique=False)
    op.create_table('finding_sets',
    sa.Column('finding_set_id', sa.String(), nullable=False),
    sa.Column('artifact_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('scope_id', sa.String(), nullable=False),
    sa.Column('scope_slug', sa.String(), nullable=False),
    sa.Column('routine_name', sa.String(), server_default='', nullable=False),
    sa.Column('revisions', sa.Text(), nullable=False),
    sa.Column('measurement', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['artifact_id'], ['artifacts.artifact_id'], ),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.chunk_id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], name='fk_finding_sets_projects'),
    sa.ForeignKeyConstraint(['scope_id'], ['scopes.scope_id'], name='fk_finding_sets_scopes'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_finding_sets_tenant_id'),
    sa.PrimaryKeyConstraint('finding_set_id'),
    sa.UniqueConstraint('artifact_id')
    )
    op.create_index('ix_finding_sets_tenant_chunk_id', 'finding_sets', ['tenant_id', 'chunk_id'], unique=False)
    op.create_index('ix_finding_sets_tenant_project_routine_scope', 'finding_sets', ['tenant_id', 'project_id', 'routine_name', 'scope_slug'], unique=False)
    op.create_table('garden_proposal_findings',
    sa.Column('proposal_id', sa.String(), nullable=False),
    sa.Column('finding_id', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['finding_id'], ['findings.finding_id'], ),
    sa.ForeignKeyConstraint(['proposal_id'], ['garden_proposals.proposal_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_garden_proposal_findings_tenant_id'),
    sa.PrimaryKeyConstraint('proposal_id', 'finding_id')
    )
    op.create_index('ix_garden_proposal_findings_tenant_finding_id', 'garden_proposal_findings', ['tenant_id', 'finding_id'], unique=False)
    op.create_index('ix_garden_proposal_findings_tenant_proposal_id', 'garden_proposal_findings', ['tenant_id', 'proposal_id', 'finding_id'], unique=False)
    op.create_table('graph_edges',
    sa.Column('edge_id', sa.String(), nullable=False),
    sa.Column('from_node_id', sa.String(), nullable=False),
    sa.Column('choice_id', sa.String(), nullable=False),
    sa.Column('to_node_name', sa.String(), nullable=False),
    sa.Column('prompt_addendum', sa.Text(), nullable=True),
    sa.Column('to_graph_model', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['choice_id'], ['graph_choices.choice_id'], ),
    sa.ForeignKeyConstraint(['from_node_id'], ['graph_nodes.node_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_graph_edges_tenant_id'),
    sa.PrimaryKeyConstraint('edge_id')
    )
    op.create_index('ix_graph_edges_tenant_from_node_id', 'graph_edges', ['tenant_id', 'from_node_id'], unique=False)
    op.create_table('project_repository_link_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('repository_id', sa.String(), nullable=False),
    sa.Column('linked', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['project_id', 'repository_id'], ['project_repository_links.project_id', 'project_repository_links.repository_id'], name='fk_project_repository_link_facts_links'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_project_repository_link_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_project_repository_link_facts_tenant_link', 'project_repository_link_facts', ['tenant_id', 'project_id', 'repository_id', 'id'], unique=False)
    op.create_table('project_source_link_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('work_source_id', sa.String(), nullable=False),
    sa.Column('linked', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['project_id', 'work_source_id'], ['project_source_links.project_id', 'project_source_links.work_source_id'], name='fk_project_source_link_facts_links'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_project_source_link_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_project_source_link_facts_tenant_link', 'project_source_link_facts', ['tenant_id', 'project_id', 'work_source_id', 'id'], unique=False)
    op.create_table('question_answers',
    sa.Column('question_id', sa.String(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('answered_by', sa.String(), nullable=False),
    sa.Column('answered_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'question_id'], ['questions.tenant_id', 'questions.question_id'], name='fk_question_answers_questions'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_question_answers_tenant_id'),
    sa.PrimaryKeyConstraint('tenant_id', 'question_id', name='pk_question_answers')
    )
    op.create_index('ix_question_answers_tenant_answered_at_question_id', 'question_answers', ['tenant_id', 'answered_at', 'question_id'], unique=False)
    op.create_table('route_environments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('route_id', sa.String(), nullable=False),
    sa.Column('environment_id', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['route_id'], ['route_created.route_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_route_environments_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_route_environments_tenant_route_id', 'route_environments', ['tenant_id', 'route_id'], unique=False)
    op.create_table('routine_lifecycle_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('routine_id', sa.String(), nullable=False),
    sa.Column('retired', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['routine_id'], ['routines.routine_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_routine_lifecycle_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_routine_lifecycle_facts_tenant_id', 'routine_lifecycle_facts', ['tenant_id'], unique=False)
    op.create_table('routine_scopes',
    sa.Column('routine_id', sa.String(), nullable=False),
    sa.Column('scope_id', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['routine_id'], ['routines.routine_id'], ),
    sa.ForeignKeyConstraint(['scope_id'], ['scopes.scope_id'], name='fk_routine_scopes_scopes'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_routine_scopes_tenant_id'),
    sa.PrimaryKeyConstraint('routine_id', 'scope_id', name='pk_routine_scopes')
    )
    op.create_index('ix_routine_scopes_tenant_scope_id', 'routine_scopes', ['tenant_id', 'scope_id'], unique=False)
    op.create_table('finding_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('finding_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('actor', sa.String(), nullable=True),
    sa.Column('proposal_id', sa.String(), nullable=True),
    sa.Column('superseded_by', sa.String(), nullable=True),
    sa.Column('finding_set_id', sa.String(), nullable=True),
    sa.Column('ref', sa.String(), nullable=True),
    sa.Column('tenant_id', sa.String(), nullable=False),
    sa.CheckConstraint("kind IN ('add', 'observed', 'gone', 'delivered', 'resolved', 'gone-confirmed', 'wont-fix', 'not-a-finding', 'superseded', 'reopened')", name='ck_finding_facts_kind'),
    sa.ForeignKeyConstraint(['finding_id'], ['findings.finding_id'], ),
    sa.ForeignKeyConstraint(['finding_set_id'], ['finding_sets.finding_set_id'], name='fk_finding_facts_finding_set_id_finding_sets'),
    sa.ForeignKeyConstraint(['proposal_id'], ['garden_proposals.proposal_id'], name='fk_finding_facts_proposal_id_garden_proposals'),
    sa.ForeignKeyConstraint(['superseded_by'], ['findings.finding_id'], name='fk_finding_facts_superseded_by_findings'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], name='fk_finding_facts_tenant_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_finding_facts_tenant_finding_id_id', 'finding_facts', ['tenant_id', 'finding_id', 'id'], unique=False)
    op.create_index('ix_finding_facts_tenant_finding_set_id', 'finding_facts', ['tenant_id', 'finding_set_id'], unique=False)
    op.create_index('ix_finding_facts_tenant_proposal_id', 'finding_facts', ['tenant_id', 'proposal_id'], unique=False)

    now = migration_clock().now()
    minted: dict[str, str] = {}
    op.bulk_insert(_seed_tenants, [
        {'tenant_id': _regenerate('ten_01M6GHA2BG9EKP2R0DNF1NKSVS', minted, now), 'name': 'default', 'created_at': now, 'created_by': 'migration'},
    ])
    op.bulk_insert(_seed_projects, [
        {'project_id': _regenerate('proj_01M6GHA46FZHESP0PPR229CWBG', minted, now), 'slug': 'default', 'name': 'Default', 'description': '', 'revision': 1, 'created_at': now, 'tenant_id': _regenerate('ten_01M6GHA2BG9EKP2R0DNF1NKSVS', minted, now), 'color': 'red'},
    ])
    op.bulk_insert(_seed_config_changes, [
        {'id': 1, 'recorded_at': now, 'actor': 'migration', 'door': 'migration', 'record_kind': 'project', 'record_key': _regenerate('proj_01M6GHA46FZHESP0PPR229CWBG', minted, now), 'revision': 1, 'op': 'create', 'diff': '[{"field": "slug", "old": null, "new": "default"}, {"field": "name", "old": null, "new": "Default"}, {"field": "description", "old": null, "new": ""}]', 'apply_id': None, 'tenant_id': _regenerate('ten_01M6GHA2BG9EKP2R0DNF1NKSVS', minted, now), 'project_id': _regenerate('proj_01M6GHA46FZHESP0PPR229CWBG', minted, now)},
    ])
    if op.get_bind().dialect.name == "postgresql":
        # An explicit key leaves the sequence behind it; the next insert would collide.
        op.execute(sa.text("SELECT setval(pg_get_serial_sequence('config_changes', 'id'), (SELECT max(id) FROM config_changes))"))


def downgrade() -> None:
    op.drop_index('ix_finding_facts_tenant_proposal_id', table_name='finding_facts')
    op.drop_index('ix_finding_facts_tenant_finding_set_id', table_name='finding_facts')
    op.drop_index('ix_finding_facts_tenant_finding_id_id', table_name='finding_facts')
    op.drop_table('finding_facts')
    op.drop_index('ix_routine_scopes_tenant_scope_id', table_name='routine_scopes')
    op.drop_table('routine_scopes')
    op.drop_index('ix_routine_lifecycle_facts_tenant_id', table_name='routine_lifecycle_facts')
    op.drop_table('routine_lifecycle_facts')
    op.drop_index('ix_route_environments_tenant_route_id', table_name='route_environments')
    op.drop_table('route_environments')
    op.drop_index('ix_question_answers_tenant_answered_at_question_id', table_name='question_answers')
    op.drop_table('question_answers')
    op.drop_index('ix_project_source_link_facts_tenant_link', table_name='project_source_link_facts')
    op.drop_table('project_source_link_facts')
    op.drop_index('ix_project_repository_link_facts_tenant_link', table_name='project_repository_link_facts')
    op.drop_table('project_repository_link_facts')
    op.drop_index('ix_graph_edges_tenant_from_node_id', table_name='graph_edges')
    op.drop_table('graph_edges')
    op.drop_index('ix_garden_proposal_findings_tenant_proposal_id', table_name='garden_proposal_findings')
    op.drop_index('ix_garden_proposal_findings_tenant_finding_id', table_name='garden_proposal_findings')
    op.drop_table('garden_proposal_findings')
    op.drop_index('ix_finding_sets_tenant_project_routine_scope', table_name='finding_sets')
    op.drop_index('ix_finding_sets_tenant_chunk_id', table_name='finding_sets')
    op.drop_table('finding_sets')
    op.drop_index('ix_decision_resolutions_tenant_resolved_at_decision_id', table_name='decision_resolutions')
    op.drop_table('decision_resolutions')
    op.drop_index('ix_close_intent_attempts_tenant_intent_id', table_name='close_intent_attempts')
    op.drop_table('close_intent_attempts')
    op.drop_index('ix_answer_deliveries_tenant_question_id', table_name='answer_deliveries')
    op.drop_table('answer_deliveries')
    op.drop_index('ix_work_source_lifecycle_facts_tenant_id', table_name='work_source_lifecycle_facts')
    op.drop_table('work_source_lifecycle_facts')
    op.drop_table('work_source_former_slugs')
    op.drop_index('ix_work_item_runs_tenant_id', table_name='work_item_runs')
    op.drop_table('work_item_runs')
    op.drop_index('ix_work_item_closures_tenant_id', table_name='work_item_closures')
    op.drop_table('work_item_closures')
    op.drop_index('ix_usage_facts_tenant_recorded_at_id', table_name='usage_facts')
    op.drop_index('ix_usage_facts_tenant_node_id', table_name='usage_facts')
    op.drop_index('ix_usage_facts_tenant_chunk_id', table_name='usage_facts')
    op.drop_index('ix_usage_facts_recorded_at_id', table_name='usage_facts')
    op.drop_table('usage_facts')
    op.drop_index('ix_transitions_tenant_to_node_id', table_name='transitions')
    op.drop_index('ix_transitions_tenant_recorded_at_transition_id', table_name='transitions')
    op.drop_index('ix_transitions_tenant_chunk_id_recorded_at', table_name='transitions')
    op.drop_index('ix_transitions_tenant_chunk_id_epoch', table_name='transitions')
    op.drop_index('ix_transitions_recorded_at_transition_id', table_name='transitions')
    op.drop_table('transitions')
    op.drop_index('ix_transcript_segments_tenant_supersedes', table_name='transcript_segments')
    op.drop_index('ix_transcript_segments_tenant_runner_received_at', table_name='transcript_segments')
    op.drop_index('ix_transcript_segments_tenant_chunk_id', table_name='transcript_segments')
    op.drop_index('ix_transcript_segments_final_chunk_id', table_name='transcript_segments')
    op.drop_table('transcript_segments')
    op.drop_index('ix_transcript_events_tenant_tool', table_name='transcript_events')
    op.drop_index('ix_transcript_events_tenant_subject', table_name='transcript_events')
    op.drop_index('ix_transcript_events_tenant_chunk_id', table_name='transcript_events')
    op.drop_index('ix_transcript_events_extractor_version_id', table_name='transcript_events')
    op.drop_table('transcript_events')
    op.drop_index('ix_scope_lifecycle_facts_tenant_id', table_name='scope_lifecycle_facts')
    op.drop_table('scope_lifecycle_facts')
    op.drop_index(op.f('ix_runner_token_revocations_token_hash'), table_name='runner_token_revocations')
    op.drop_index('ix_runner_token_revocations_tenant_id', table_name='runner_token_revocations')
    op.drop_table('runner_token_revocations')
    op.drop_index('ix_runner_pause_facts_tenant_id', table_name='runner_pause_facts')
    op.drop_table('runner_pause_facts')
    op.drop_index('ix_runner_lifecycle_facts_tenant_id', table_name='runner_lifecycle_facts')
    op.drop_table('runner_lifecycle_facts')
    op.drop_index('ix_routines_tenant_name', table_name='routines')
    op.drop_table('routines')
    op.drop_index('ix_route_token_minted_tenant_chunk_id', table_name='route_token_minted')
    op.drop_table('route_token_minted')
    op.drop_index('ix_route_released_tenant_released_at_id', table_name='route_released')
    op.drop_index('ix_route_released_tenant_chunk_id', table_name='route_released')
    op.drop_index('ix_route_released_released_at_id', table_name='route_released')
    op.drop_table('route_released')
    op.drop_index('ix_route_created_tenant_runner_id', table_name='route_created')
    op.drop_index('ix_route_created_tenant_created_at_route_id', table_name='route_created')
    op.drop_index('ix_route_created_tenant_chunk_id', table_name='route_created')
    op.drop_table('route_created')
    op.drop_index('ix_requeues_tenant_requeued_at_id', table_name='requeues')
    op.drop_index('ix_requeues_tenant_chunk_id', table_name='requeues')
    op.drop_table('requeues')
    op.drop_index('ix_repository_lifecycle_facts_tenant_id', table_name='repository_lifecycle_facts')
    op.drop_table('repository_lifecycle_facts')
    op.drop_table('repository_former_slugs')
    op.drop_index('ix_queue_positions_tenant_chunk_id', table_name='queue_positions')
    op.drop_table('queue_positions')
    op.drop_index('ix_questions_tenant_project_asked_at_question_id', table_name='questions')
    op.drop_index('ix_questions_tenant_chunk_id', table_name='questions')
    op.drop_index('ix_questions_tenant_asked_at_question_id', table_name='questions')
    op.drop_table('questions')
    op.drop_index('ix_project_source_links_tenant_source', table_name='project_source_links')
    op.drop_table('project_source_links')
    op.drop_index('ix_project_repository_links_tenant_repository', table_name='project_repository_links')
    op.drop_table('project_repository_links')
    op.drop_index('ix_lease_facts_tenant_minted_at', table_name='lease_facts')
    op.drop_index('ix_lease_facts_tenant_chunk_id_epoch', table_name='lease_facts')
    op.drop_index('ix_lease_facts_minted_at', table_name='lease_facts')
    op.drop_table('lease_facts')
    op.drop_index('ix_hub_node_poll_tenant_chunk_id', table_name='hub_node_poll')
    op.drop_table('hub_node_poll')
    op.drop_index('ix_hub_exec_slot_tenant_id', table_name='hub_exec_slot')
    op.drop_table('hub_exec_slot')
    op.drop_index('ix_graph_choices_tenant_node_id', table_name='graph_choices')
    op.drop_table('graph_choices')
    op.drop_index('ix_garden_proposal_closures_tenant_source_ref', table_name='garden_proposal_closures')
    op.drop_table('garden_proposal_closures')
    op.drop_index('ix_findings_tenant_project_scope_source', table_name='findings')
    op.drop_index('ix_findings_tenant_project_routine_scope', table_name='findings')
    op.drop_index('ix_findings_tenant_project_routine_class', table_name='findings')
    op.drop_table('findings')
    op.drop_index('ix_event_log_tenant_recorded_at', table_name='event_log')
    op.drop_table('event_log')
    op.drop_index('ix_escalations_tenant_recorded_at_id', table_name='escalations')
    op.drop_index('ix_escalations_tenant_chunk_id', table_name='escalations')
    op.drop_index('ix_escalations_recorded_at_id', table_name='escalations')
    op.drop_table('escalations')
    op.drop_index('ix_epoch_owners_tenant_recorded_at_id', table_name='epoch_owners')
    op.drop_index('ix_epoch_owners_tenant_chunk_id', table_name='epoch_owners')
    op.drop_index('ix_epoch_owners_recorded_at_id', table_name='epoch_owners')
    op.drop_table('epoch_owners')
    op.drop_index('ix_delivery_repo_landed_tenant_chunk_id', table_name='delivery_repo_landed')
    op.drop_table('delivery_repo_landed')
    op.drop_index('ix_delivery_landed_tenant_chunk_id', table_name='delivery_landed')
    op.drop_table('delivery_landed')
    op.drop_index('ix_decisions_tenant_submitted_at_decision_id', table_name='decisions')
    op.drop_index('ix_decisions_tenant_project_submitted_at_decision_id', table_name='decisions')
    op.drop_index('ix_decisions_tenant_chunk_id', table_name='decisions')
    op.drop_index('ix_decisions_submitted_at_decision_id', table_name='decisions')
    op.drop_table('decisions')
    op.drop_index('ix_close_intents_tenant_id', table_name='close_intents')
    op.drop_index('ix_close_intents_pending', table_name='close_intents')
    op.drop_table('close_intents')
    op.drop_index('ix_chunk_work_refs_tenant_source_ref', table_name='chunk_work_refs')
    op.drop_index('ix_chunk_work_refs_tenant_chunk_id', table_name='chunk_work_refs')
    op.drop_table('chunk_work_refs')
    op.drop_index('ix_chunk_stopped_tenant_stopped_at_id', table_name='chunk_stopped')
    op.drop_index('ix_chunk_stopped_tenant_chunk_id', table_name='chunk_stopped')
    op.drop_index('ix_chunk_stopped_stopped_at_id', table_name='chunk_stopped')
    op.drop_table('chunk_stopped')
    op.drop_index('ix_chunk_restarts_tenant_recorded_at_id', table_name='chunk_restarts')
    op.drop_index('ix_chunk_restarts_tenant_chunk_id_recorded_at', table_name='chunk_restarts')
    op.drop_index('ix_chunk_restarts_tenant_chunk_id', table_name='chunk_restarts')
    op.drop_index('ix_chunk_restarts_recorded_at_id', table_name='chunk_restarts')
    op.drop_table('chunk_restarts')
    op.drop_index('ix_chunk_promoted_tenant_promoted_at_id', table_name='chunk_promoted')
    op.drop_index('ix_chunk_promoted_tenant_chunk_id', table_name='chunk_promoted')
    op.drop_table('chunk_promoted')
    op.drop_index('ix_chunk_pause_facts_tenant_set_at_id', table_name='chunk_pause_facts')
    op.drop_index('ix_chunk_pause_facts_tenant_chunk_id', table_name='chunk_pause_facts')
    op.drop_table('chunk_pause_facts')
    op.drop_index('ix_chunk_migrations_tenant_recorded_at_migration_id', table_name='chunk_migrations')
    op.drop_index('ix_chunk_migrations_tenant_chunk_id_recorded_at', table_name='chunk_migrations')
    op.drop_index('ix_chunk_migrations_tenant_chunk_id', table_name='chunk_migrations')
    op.drop_index('ix_chunk_migrations_recorded_at_migration_id', table_name='chunk_migrations')
    op.drop_table('chunk_migrations')
    op.drop_index('ix_chunk_grouped_tenant_grouped_at_id', table_name='chunk_grouped')
    op.drop_table('chunk_grouped')
    op.drop_index('ix_chunk_dependencies_tenant_prerequisite_chunk_id', table_name='chunk_dependencies')
    op.drop_index('ix_chunk_dependencies_tenant_dependent_chunk_id', table_name='chunk_dependencies')
    op.drop_table('chunk_dependencies')
    op.drop_index('ix_chunk_demoted_tenant_demoted_at_id', table_name='chunk_demoted')
    op.drop_index('ix_chunk_demoted_tenant_chunk_id', table_name='chunk_demoted')
    op.drop_table('chunk_demoted')
    op.drop_index('ix_chunk_deleted_tenant_deleted_at_id', table_name='chunk_deleted')
    op.drop_table('chunk_deleted')
    op.drop_index('ix_chunk_completed_tenant_completed_at_id', table_name='chunk_completed')
    op.drop_index('ix_chunk_completed_tenant_chunk_id', table_name='chunk_completed')
    op.drop_index('ix_chunk_completed_completed_at_id', table_name='chunk_completed')
    op.drop_table('chunk_completed')
    op.drop_index('ix_chunk_bounces_tenant_chunk_id_epoch', table_name='chunk_bounces')
    op.drop_table('chunk_bounces')
    op.drop_index('ix_artifacts_tenant_chunk_id_node_id_epoch', table_name='artifacts')
    op.drop_table('artifacts')
    op.drop_table('work_sources')
    op.drop_index('ix_work_items_tenant_source', table_name='work_items')
    op.drop_table('work_items')
    op.drop_index('ix_secret_lifecycle_facts_tenant_id', table_name='secret_lifecycle_facts')
    op.drop_table('secret_lifecycle_facts')
    op.drop_index('ix_scopes_tenant_slug', table_name='scopes')
    op.drop_table('scopes')
    op.drop_index(op.f('ix_runner_registrations_token_hash'), table_name='runner_registrations')
    op.drop_index('ix_runner_registrations_tenant_id', table_name='runner_registrations')
    op.drop_index('ix_runner_registrations_tenant_host_id', table_name='runner_registrations')
    op.drop_table('runner_registrations')
    op.drop_table('repositories')
    op.drop_table('project_former_slugs')
    op.drop_index('ix_invitation_facts_tenant_id', table_name='invitation_facts')
    op.drop_table('invitation_facts')
    op.drop_index(op.f('ix_host_token_revocations_token_hash'), table_name='host_token_revocations')
    op.drop_index('ix_host_token_revocations_tenant_id', table_name='host_token_revocations')
    op.drop_table('host_token_revocations')
    op.drop_index('ix_host_lifecycle_facts_tenant_id', table_name='host_lifecycle_facts')
    op.drop_table('host_lifecycle_facts')
    op.drop_index('ix_graph_sessions_tenant_graph_id', table_name='graph_sessions')
    op.drop_table('graph_sessions')
    op.drop_index('ix_graph_policy_facts_tenant_id', table_name='graph_policy_facts')
    op.drop_table('graph_policy_facts')
    op.drop_index('ix_graph_nodes_tenant_graph_id', table_name='graph_nodes')
    op.drop_table('graph_nodes')
    op.drop_index('ix_graph_lifecycle_facts_tenant_graph_id', table_name='graph_lifecycle_facts')
    op.drop_table('graph_lifecycle_facts')
    op.drop_index('ix_graph_artifacts_tenant_graph_id', table_name='graph_artifacts')
    op.drop_table('graph_artifacts')
    op.drop_index('ux_garden_proposals_source_artifact_ref', table_name='garden_proposals')
    op.drop_index('ix_garden_proposals_tenant_project_routine_class', table_name='garden_proposals')
    op.drop_table('garden_proposals')
    op.drop_index('ix_config_changes_tenant_record', table_name='config_changes')
    op.drop_index('ix_config_changes_tenant_project_id', table_name='config_changes')
    op.drop_table('config_changes')
    op.drop_index('ix_chunks_tenant_project_minted_at_chunk_id', table_name='chunks')
    op.drop_index('ix_chunks_tenant_minted_at_chunk_id', table_name='chunks')
    op.drop_table('chunks')
    op.drop_table('work_item_sequence')
    op.drop_index('ix_transcript_high_water_tenant_id', table_name='transcript_high_water')
    op.drop_table('transcript_high_water')
    op.drop_index('ix_transcript_event_drops_tenant_dropped_at_segment_id', table_name='transcript_event_drops')
    op.drop_index('ix_transcript_event_drops_tenant_chunk_id_epoch', table_name='transcript_event_drops')
    op.drop_index('ix_transcript_event_drops_egress_order', table_name='transcript_event_drops')
    op.drop_table('transcript_event_drops')
    op.drop_index('ix_transcript_event_derivations_tenant_derived_at', table_name='transcript_event_derivations')
    op.drop_index('ix_transcript_event_derivations_egress_order', table_name='transcript_event_derivations')
    op.drop_table('transcript_event_derivations')
    op.drop_table('superuser_bootstrap')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_table('secrets')
    op.drop_index('ix_runner_local_pause_facts_tenant_id', table_name='runner_local_pause_facts')
    op.drop_table('runner_local_pause_facts')
    op.drop_index('ix_runner_high_water_tenant_id', table_name='runner_high_water')
    op.drop_table('runner_high_water')
    op.drop_index('ix_runner_external_usage_misses_tenant_id', table_name='runner_external_usage_misses')
    op.drop_table('runner_external_usage_misses')
    op.drop_index('ix_runner_external_usage_tenant_id', table_name='runner_external_usage')
    op.drop_table('runner_external_usage')
    op.drop_table('projects')
    op.drop_index('ix_membership_facts_user_id', table_name='membership_facts')
    op.drop_index('ix_membership_facts_tenant_user', table_name='membership_facts')
    op.drop_table('membership_facts')
    op.drop_table('keyed_locks')
    op.drop_index('ix_invitations_token_hash', table_name='invitations')
    op.drop_index('ix_invitations_tenant_id', table_name='invitations')
    op.drop_table('invitations')
    op.drop_index(op.f('ix_identities_user_id'), table_name='identities')
    op.drop_table('identities')
    op.drop_index('ix_hub_admin_facts_user_id', table_name='hub_admin_facts')
    op.drop_table('hub_admin_facts')
    op.drop_index(op.f('ix_hosts_token_hash'), table_name='hosts')
    op.drop_index('ix_hosts_tenant_id', table_name='hosts')
    op.drop_table('hosts')
    op.drop_index('ix_host_high_water_tenant_id', table_name='host_high_water')
    op.drop_table('host_high_water')
    op.drop_index('ix_host_external_usage_misses_tenant_id', table_name='host_external_usage_misses')
    op.drop_table('host_external_usage_misses')
    op.drop_index('ix_host_external_usage_tenant_id', table_name='host_external_usage')
    op.drop_table('host_external_usage')
    op.drop_index('ix_graphs_tenant_name', table_name='graphs')
    op.drop_table('graphs')
    op.drop_index('ix_forge_annotation_facts_tenant_work_source_id_id', table_name='forge_annotation_facts')
    op.drop_table('forge_annotation_facts')
    op.drop_index('uq_users_email', table_name='users', sqlite_where=sa.text('email IS NOT NULL'), postgresql_where=sa.text('email IS NOT NULL'))
    op.drop_table('users')
    op.drop_index('ix_trace_cursor_recorded_at_id', table_name='trace_cursor')
    op.drop_table('trace_cursor')
    op.drop_table('tenants')
    op.drop_table('tenant_deletions')
    op.drop_index('ix_hub_event_log_recorded_at', table_name='hub_event_log')
    op.drop_index('ix_hub_event_log_kind_recorded_at', table_name='hub_event_log')
    op.drop_table('hub_event_log')
    op.drop_index('ix_egress_cursor_dataset_recorded_at_id', table_name='egress_cursor')
    op.drop_table('egress_cursor')
    op.drop_table('config_import_facts')
    op.drop_table('auth_state')
    op.drop_table('auth_facts')
