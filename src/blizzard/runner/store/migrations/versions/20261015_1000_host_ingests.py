"""runner store baseline — the history up to v0.2.0 as one revision.

Creates the schema v0.2.0 declares, and inserts the rows its history seeds. An id minted by a seeded row and the instant a seeded row is stamped with are minted and stamped afresh when this runs, each id once wherever it recurs; every other value is the one the history wrote. A store at a revision in ``ABSORBED`` is refused, naming ``BASELINE_FLOOR``.

Revision ID: 20261015_1000_host_ingests
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261015_1000_host_ingests"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASELINE_FLOOR = "v0.2.0"
ABSORBED = frozenset(
    {
        "20260713_1112_runner_initial",
        "20260713_1218_runner_walking_skeleton",
        "20260713_1245_runner_lease_lifecycle",
        "20260713_1635_runner_heartbeats",
        "20260713_1801_runner_asks_and_parks",
        "20260713_1946_runner_hub_control",
        "20260714_1656_runner_resume_intents",
        "20260715_1633_runner_workspace_prompt",
        "20260715_1641_runner_session_ends",
        "20260716_0532_runner_crash_recovery_context",
        "20260716_1511_runner_local_pause",
        "20260717_0446_runner_pause_parks",
        "20260717_2200_runner_usage_facts",
        "20260717_2245_runner_takeovers",
        "20260717_2300_runner_requeues",
        "20260718_1200_runner_route_tokens",
        "20260719_0900_runner_lease_tokens",
        "20260719_1000_runner_attachments",
        "20260719_1100_runner_nudge_facts",
        "20260721_1500_runner_jwt_jti_seen",
        "20260722_1000_runner_git_commit_declarations",
        "20260725_1200_runner_check_results",
        "20260726_1000_runner_declaration_environment_id",
        "20260727_1000_runner_session_preamble_facts",
        "20260728_1500_runner_lease_session_stamps",
        "20260801_1500_runner_external_usage_samples",
        "20260808_2300_runner_transcript_segments",
        "20260809_2330_runner_transcript_sidechain_warned_agents",
        "20260810_1200_runner_transcript_truncated_reasons_warned",
        "20260811_0900_runner_transcript_segment_supersedes",
        "20260811_1000_runner_context_samples",
        "20260811_1400_runner_escalation_closures",
        "20260816_1100_runner_transcript_agent_tool_use_ids",
        "20260817_1000_runner_graph_artifacts",
        "20260818_0900_runner_lease_compaction_window",
        "20260831_1000_runner_in_flight_elicitations",
        "20260905_1000_runner_external_usage_samples_slug",
        "20260913_1100_runner_store_indexes",
        "20260914_1000_session_harness_provenance",
        "20260916_1000_two_phase_spawn_ownership",
        "20260917_1200_elicitation_process_group",
        "20260918_1000_invocation_boundaries",
        "20260919_1000_transcript_segment_provenance",
        "20260919_1400_selftest_results",
        "20260920_0100_usage_reported_cost",
        "20260920_0200_usage_cost_is_share",
        "20260920_0300_usage_harness_provenance",
        "20260921_1000_local_pause_reason",
        "20260921_1600_overload_facts",
        "20260922_1200_external_usage_miss_reason",
        "20260924_0400_pause_park_interrupted_elicitation",
        "20260924_0500_in_flight_elicitations_autoincrement",
        "20260925_1000_runner_lease_graph_name_work_refs",
        "20261002_1000_runner_usage_facts_estimated_cost",
        "20261002_1100_runner_trace_cursor",
        "20261002_1200_runner_trace_latch_kind_index",
        "20261002_1300_runner_lease_tokens_token_hash_index",
        "20261003_1000_runner_transcript_segment_spawn_cwd",
        "20261004_1000_binding_release_epoch_floor",
        "20261004_1000_runner_invocation_boundary_advances",
        "20261004_1100_runner_lease_closure_escalation_cause",
        "20261004_1200_runner_takeover_hold_epoch",
        "20261005_1000_runner_credential_renewal_facts",
        "20261006_1000_runner_identity",
        "20261008_1000_runner_identity_tenant",
        "20261008_1100_runner_hub_projects",
        "20261009_1000_runner_lease_closures_lease_id_index",
        "20261011_1000_runner_scope",
        "20261013_1000_runner_scope_tighten",
    }
)


def upgrade() -> None:
    op.create_table('asks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('question_id', sa.String(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('options', sa.Text(), nullable=False),
    sa.Column('session_id', sa.String(), nullable=True),
    sa.Column('harness_id', sa.String(), nullable=True),
    sa.Column('asked_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('attachments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('attached_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_attachments_lease_id_name_id', 'attachments', ['lease_id', 'name', 'id'], unique=False)
    op.create_table('binding_releases',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('environment_id', sa.String(), nullable=False),
    sa.Column('released_at', UtcDateTime(), nullable=False),
    sa.Column('lease_epoch_floor', sa.Integer(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_binding_releases_chunk_id_environment_id_released_at', 'binding_releases', ['chunk_id', 'environment_id', 'released_at'], unique=False)
    op.create_table('check_results',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('command', sa.Text(), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('output_tail', sa.Text(), nullable=False),
    sa.Column('ran_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('checks_ran',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('ran_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('context_samples',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('session_id', sa.String(), nullable=False),
    sa.Column('harness_id', sa.String(), nullable=False),
    sa.Column('context_tokens', sa.Integer(), nullable=True),
    sa.Column('sampled_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_index(op.f('ix_context_samples_lease_id'), 'context_samples', ['lease_id'], unique=False)
    op.create_table('credential_renewal_claims',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('claimed_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'host', 'transfer': 'append'}
    )
    op.create_index('ix_credential_renewal_claims_slug_claimed_at', 'credential_renewal_claims', ['slug', 'claimed_at'], unique=False)
    op.create_table('credential_renewal_outcomes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('failure_reason', sa.String(), nullable=True),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.CheckConstraint("failure_reason IS NULL OR failure_reason IN ('renewer_unavailable', 'timed_out', 'vendor_refused', 'protocol_error')", name='ck_credential_renewal_outcomes_failure_reason'),
    sa.CheckConstraint("kind IN ('renewed', 'failed')", name='ck_credential_renewal_outcomes_kind'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('claim_id'),
    info={'scope': 'host', 'transfer': 'append'}
    )
    op.create_table('daemon_liveness',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('alive_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'host', 'transfer': 'none'}
    )
    op.create_table('env_bindings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('environment_id', sa.String(), nullable=False),
    sa.Column('workdir', sa.String(), nullable=False),
    sa.Column('bound_at', UtcDateTime(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=True),
    sa.Column('project_id', sa.String(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('escalation_closures',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('closed_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('external_usage_samples',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('sampled_at', UtcDateTime(), nullable=False),
    sa.Column('payload', sa.Text(), nullable=True),
    sa.Column('miss_reason', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'host', 'transfer': 'append'}
    )
    op.create_index('ix_external_usage_samples_slug_sampled_at', 'external_usage_samples', ['slug', 'sampled_at'], unique=False)
    op.create_table('git_commit_declarations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('environment_id', sa.String(), nullable=False),
    sa.Column('repo', sa.String(), nullable=False),
    sa.Column('branch', sa.String(), nullable=False),
    sa.Column('commit', sa.String(), nullable=False),
    sa.Column('declared_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('graph_artifacts',
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('graph_id', 'name'),
    info={'scope': 'host', 'transfer': 'merge'}
    )
    op.create_table('heartbeats',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('beat_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_heartbeats_lease_id_beat_at', 'heartbeats', ['lease_id', 'beat_at'], unique=False)
    op.create_table('host_high_water_seen',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'host', 'transfer': 'none'}
    )
    op.create_table('host_identity',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('host_id', sa.String(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=True),
    sa.Column('registered_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'host', 'transfer': 'none'}
    )
    op.create_table('host_ingests',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('source_alive_at', UtcDateTime(), nullable=True),
    sa.Column('ingested_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'host', 'transfer': 'none'}
    )
    op.create_table('host_outbound_buffer',
    sa.Column('seq', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('acked_at', UtcDateTime(), nullable=True),
    sa.PrimaryKeyConstraint('seq'),
    info={'scope': 'host', 'transfer': 'none'},
    sqlite_autoincrement=True
    )
    op.create_table('hub_control',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('paused', sa.Boolean(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('runner_id', name='uq_hub_control_runner_id'),
    info={'scope': 'runner'}
    )
    op.create_table('hub_served_projects',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('entry', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=True),
    sa.Column('slug', sa.String(), nullable=True),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('answered_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('runner_id', 'position', name='uq_hub_served_projects_runner_id_position'),
    info={'scope': 'runner'}
    )
    op.create_table('in_flight_elicitations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=True),
    sa.Column('process_start_time', sa.String(), nullable=True),
    sa.Column('pgid', sa.Integer(), nullable=True),
    sa.Column('output_path', sa.String(), nullable=False),
    sa.Column('first_launched_at', UtcDateTime(), nullable=False),
    sa.Column('relaunch_count', sa.Integer(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'},
    sqlite_autoincrement=True
    )
    op.create_table('invocation_boundaries',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('start_position', sa.String(), nullable=True),
    sa.Column('start_unreadable', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('opened_at', UtcDateTime(), nullable=False),
    sa.Column('closed_at', UtcDateTime(), nullable=True),
    sa.Column('closed_reason', sa.String(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_invocation_boundaries_lease_id', 'invocation_boundaries', ['lease_id'], unique=False)
    op.create_table('invocation_boundary_advances',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('superseded_invocation', sa.String(), nullable=False),
    sa.Column('start_position', sa.String(), nullable=True),
    sa.Column('start_unreadable', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('advanced_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_invocation_boundary_advances_boundary', 'invocation_boundary_advances', ['lease_id', 'generation', 'kind'], unique=False)
    op.create_table('jwt_jti_seen',
    sa.Column('jti', sa.String(), nullable=False),
    sa.Column('aud', sa.String(), nullable=False),
    sa.Column('expires_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('jti'),
    info={'scope': 'host', 'transfer': 'none'}
    )
    op.create_table('lease_closures',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('closed_at', UtcDateTime(), nullable=False),
    sa.Column('escalation_cause', sa.String(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_lease_closures_closed_at_lease_id', 'lease_closures', ['closed_at', 'lease_id'], unique=False)
    op.create_index('ix_lease_closures_lease_id_id', 'lease_closures', ['lease_id', 'id'], unique=False)
    op.create_table('lease_context',
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('graph_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('node_name', sa.String(), nullable=False),
    sa.Column('retries_max', sa.Integer(), nullable=False),
    sa.Column('session_name', sa.String(), nullable=True),
    sa.Column('resolved_model', sa.String(), nullable=True),
    sa.Column('resolved_effort', sa.String(), nullable=True),
    sa.Column('resolved_compaction_window', sa.String(), nullable=True),
    sa.Column('graph_name', sa.String(), nullable=True),
    sa.Column('work_refs', sa.Text(), nullable=True),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('lease_id'),
    info={'scope': 'runner'}
    )
    op.create_table('lease_spawns',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('spawned_at', UtcDateTime(), nullable=False),
    sa.Column('harness_id', sa.String(), nullable=True),
    sa.Column('harness_version', sa.String(), nullable=True),
    sa.Column('pid', sa.Integer(), nullable=True),
    sa.Column('process_start_time', sa.String(), nullable=True),
    sa.Column('pgid', sa.Integer(), nullable=True),
    sa.Column('session_id', sa.String(), nullable=True),
    sa.Column('identified_at', UtcDateTime(), nullable=True),
    sa.Column('identity_failed_at', UtcDateTime(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('lease_tokens',
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('minted_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('lease_id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_lease_tokens_token_hash', 'lease_tokens', ['token_hash'], unique=False)
    op.create_table('leases',
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=True),
    sa.Column('process_start_time', sa.String(), nullable=True),
    sa.Column('session_id', sa.String(), nullable=True),
    sa.Column('harness_id', sa.String(), nullable=True),
    sa.Column('pgid', sa.Integer(), nullable=True),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('lease_id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_leases_chunk_id_epoch', 'leases', ['chunk_id', 'epoch'], unique=False)
    op.create_table('local_pause_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('paused', sa.Boolean(), nullable=False),
    sa.Column('set_at', UtcDateTime(), nullable=False),
    sa.Column('set_by', sa.String(), nullable=False),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('nudge_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('nudged_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('outbound_buffer',
    sa.Column('seq', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=True),
    sa.Column('lease_id', sa.String(), nullable=True),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('acked_at', UtcDateTime(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('runner_id', 'seq', name='pk_outbound_buffer'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_outbound_buffer_acked_at_seq', 'outbound_buffer', ['acked_at', 'seq'], unique=False)
    op.create_table('outbox_counters',
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('lane', sa.String(), nullable=False),
    sa.Column('last_seq', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('runner_id', 'lane', name='pk_outbox_counters'),
    info={'scope': 'runner'}
    )
    op.create_table('overload_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('invocation_kind', sa.String(), nullable=False),
    sa.Column('invocation_identity', sa.String(), nullable=False),
    sa.Column('streak_ordinal', sa.Integer(), nullable=False),
    sa.Column('observed_at', UtcDateTime(), nullable=False),
    sa.Column('resume_after', UtcDateTime(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('overload_resets',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('reset_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('park_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('question_id', sa.String(), nullable=False),
    sa.Column('parked_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('park_resumes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('question_id', sa.String(), nullable=False),
    sa.Column('resumed_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('pause_park_resumes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('resumed_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('pause_parks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('parked_at', UtcDateTime(), nullable=False),
    sa.Column('interrupted_elicitation_id', sa.Integer(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('requeues',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('requeued_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('resume_clears',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('cleared_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('resume_intents',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('marked_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('route_tokens',
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('token', sa.Text(), nullable=False),
    sa.Column('acquired_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('runner_id', 'chunk_id', name='pk_route_tokens'),
    info={'scope': 'runner'}
    )
    op.create_table('runner_identity',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.Column('runner_name', sa.String(), nullable=False),
    sa.Column('registered_at', UtcDateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(), nullable=True),
    sa.Column('tenant_name', sa.String(), nullable=True),
    sa.Column('projects_answered_at', UtcDateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('runner_id', name='uq_runner_identity_runner_id'),
    info={'scope': 'runner'}
    )
    op.create_table('selftest_results',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('harness_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('session_ends',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('ended_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('session_preamble_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('session_id', sa.String(), nullable=False),
    sa.Column('harness_id', sa.String(), nullable=False),
    sa.Column('blizzard_digest', sa.String(), nullable=False),
    sa.Column('workspace_digest', sa.String(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('takeover_ends',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('takeover_id', sa.String(), nullable=False),
    sa.Column('ended_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('takeovers',
    sa.Column('takeover_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('lease_id', sa.String(), nullable=True),
    sa.Column('session_id', sa.String(), nullable=True),
    sa.Column('harness_id', sa.String(), nullable=True),
    sa.Column('workdir', sa.String(), nullable=False),
    sa.Column('fence_epoch', sa.Integer(), nullable=True),
    sa.Column('hold_epoch', sa.Integer(), nullable=True),
    sa.Column('opened_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('takeover_id'),
    info={'scope': 'runner'}
    )
    op.create_table('trace_cursor',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('position_at', UtcDateTime(), nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('span_count', sa.Integer(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_trace_cursor_recorded_at_id', 'trace_cursor', ['recorded_at', 'id'], unique=False)
    op.create_table('trace_export_latch',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_trace_export_latch_kind_recorded_at_id', 'trace_export_latch', ['kind', 'recorded_at', 'id'], unique=False)
    op.create_index('ix_trace_export_latch_recorded_at_id', 'trace_export_latch', ['recorded_at', 'id'], unique=False)
    op.create_table('transcript_outbound_buffer',
    sa.Column('seq', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('segment_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('final', sa.Boolean(), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('created_at', UtcDateTime(), nullable=False),
    sa.Column('acked_at', UtcDateTime(), nullable=True),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('runner_id', 'seq', name='pk_transcript_outbound_buffer'),
    info={'scope': 'runner'}
    )
    op.create_table('transcript_segments',
    sa.Column('segment_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('session_id', sa.String(), nullable=False),
    sa.Column('harness_id', sa.String(), nullable=False),
    sa.Column('cursor', sa.String(), nullable=True),
    sa.Column('shipped_bytes', sa.Integer(), nullable=False),
    sa.Column('shipped_turns', sa.Integer(), nullable=False),
    sa.Column('normalizer_version', sa.String(), nullable=False),
    sa.Column('harness_version', sa.String(), nullable=True),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('effort', sa.String(), nullable=True),
    sa.Column('spawn_cwd', sa.String(), nullable=True),
    sa.Column('truncated_reason', sa.String(), nullable=True),
    sa.Column('truncated_reason_severity', sa.Integer(), nullable=True),
    sa.Column('shipping_stopped_reason', sa.String(), nullable=True),
    sa.Column('sidechain_warned_agents', sa.Text(), nullable=True),
    sa.Column('agent_tool_use_ids', sa.Text(), nullable=True),
    sa.Column('truncated_reasons_warned', sa.Text(), nullable=True),
    sa.Column('supersedes', sa.String(), nullable=True),
    sa.Column('finalized_at', UtcDateTime(), nullable=True),
    sa.Column('stamped_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('segment_id'),
    info={'scope': 'runner'}
    )
    op.create_index('ix_transcript_segments_chunk_id_stamped_at_segment_id', 'transcript_segments', ['chunk_id', 'stamped_at', 'segment_id'], unique=False)
    op.create_table('usage_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lease_id', sa.String(), nullable=False),
    sa.Column('chunk_id', sa.String(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('epoch', sa.Integer(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('model', sa.String(), nullable=False),
    sa.Column('harness_id', sa.String(), nullable=True),
    sa.Column('harness_version', sa.String(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_create_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('reported_cost_usd', sa.Float(), nullable=True),
    sa.Column('estimated_cost_usd', sa.Float(), nullable=True),
    sa.Column('cost_is_share', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('recorded_at', UtcDateTime(), nullable=False),
    sa.Column('runner_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    info={'scope': 'runner'}
    )
    op.create_table('workspace_prompt',
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('updated_at', UtcDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('workspace_id'),
    info={'scope': 'host', 'transfer': 'workspace'}
    )


def downgrade() -> None:
    op.drop_table('workspace_prompt')
    op.drop_table('usage_facts')
    op.drop_index('ix_transcript_segments_chunk_id_stamped_at_segment_id', table_name='transcript_segments')
    op.drop_table('transcript_segments')
    op.drop_table('transcript_outbound_buffer')
    op.drop_index('ix_trace_export_latch_recorded_at_id', table_name='trace_export_latch')
    op.drop_index('ix_trace_export_latch_kind_recorded_at_id', table_name='trace_export_latch')
    op.drop_table('trace_export_latch')
    op.drop_index('ix_trace_cursor_recorded_at_id', table_name='trace_cursor')
    op.drop_table('trace_cursor')
    op.drop_table('takeovers')
    op.drop_table('takeover_ends')
    op.drop_table('session_preamble_facts')
    op.drop_table('session_ends')
    op.drop_table('selftest_results')
    op.drop_table('runner_identity')
    op.drop_table('route_tokens')
    op.drop_table('resume_intents')
    op.drop_table('resume_clears')
    op.drop_table('requeues')
    op.drop_table('pause_parks')
    op.drop_table('pause_park_resumes')
    op.drop_table('park_resumes')
    op.drop_table('park_facts')
    op.drop_table('overload_resets')
    op.drop_table('overload_facts')
    op.drop_table('outbox_counters')
    op.drop_index('ix_outbound_buffer_acked_at_seq', table_name='outbound_buffer')
    op.drop_table('outbound_buffer')
    op.drop_table('nudge_facts')
    op.drop_table('local_pause_facts')
    op.drop_index('ix_leases_chunk_id_epoch', table_name='leases')
    op.drop_table('leases')
    op.drop_index('ix_lease_tokens_token_hash', table_name='lease_tokens')
    op.drop_table('lease_tokens')
    op.drop_table('lease_spawns')
    op.drop_table('lease_context')
    op.drop_index('ix_lease_closures_lease_id_id', table_name='lease_closures')
    op.drop_index('ix_lease_closures_closed_at_lease_id', table_name='lease_closures')
    op.drop_table('lease_closures')
    op.drop_table('jwt_jti_seen')
    op.drop_index('ix_invocation_boundary_advances_boundary', table_name='invocation_boundary_advances')
    op.drop_table('invocation_boundary_advances')
    op.drop_index('ix_invocation_boundaries_lease_id', table_name='invocation_boundaries')
    op.drop_table('invocation_boundaries')
    op.drop_table('in_flight_elicitations')
    op.drop_table('hub_served_projects')
    op.drop_table('hub_control')
    op.drop_table('host_outbound_buffer')
    op.drop_table('host_ingests')
    op.drop_table('host_identity')
    op.drop_table('host_high_water_seen')
    op.drop_index('ix_heartbeats_lease_id_beat_at', table_name='heartbeats')
    op.drop_table('heartbeats')
    op.drop_table('graph_artifacts')
    op.drop_table('git_commit_declarations')
    op.drop_index('ix_external_usage_samples_slug_sampled_at', table_name='external_usage_samples')
    op.drop_table('external_usage_samples')
    op.drop_table('escalation_closures')
    op.drop_table('env_bindings')
    op.drop_table('daemon_liveness')
    op.drop_table('credential_renewal_outcomes')
    op.drop_index('ix_credential_renewal_claims_slug_claimed_at', table_name='credential_renewal_claims')
    op.drop_table('credential_renewal_claims')
    op.drop_index(op.f('ix_context_samples_lease_id'), table_name='context_samples')
    op.drop_table('context_samples')
    op.drop_table('checks_ran')
    op.drop_table('check_results')
    op.drop_index('ix_binding_releases_chunk_id_environment_id_released_at', table_name='binding_releases')
    op.drop_table('binding_releases')
    op.drop_index('ix_attachments_lease_id_name_id', table_name='attachments')
    op.drop_table('attachments')
    op.drop_table('asks')
