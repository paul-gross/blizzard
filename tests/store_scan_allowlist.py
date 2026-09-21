"""Store-read-index gate allow-list (blizzard#525).

``ROW_THRESHOLD`` bounds what a "deliberately unindexed" table's declared row bound may claim — an entry above it
fails the gate's own hygiene check, not a measured runtime fact (sqlite's plan is row-count-independent without
``ANALYZE``). ``RUNNER_ALLOWED_SCANS`` leans on the runner's own store being per-environment (small by lifetime);
``HUB_ALLOWED_SCANS`` cannot — the hub's store is shared fleet-wide, so each entry names its own bounded reason."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.domain.findings import IReadFindingRepository
from blizzard.runner.domain.outbound import IReadOutboundRepository
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository

#: The ceiling an allow-list entry's declared row bound must never exceed.
ROW_THRESHOLD = 200


@dataclass(frozen=True)
class TableWideAllowance:
    """A table that is small everywhere it is scanned — allowed for every read that
    scans it, not just one."""

    table: str
    row_bound: int
    reason: str


@dataclass(frozen=True)
class MethodScopedAllowance:
    """A deliberate whole-table read of a table that is otherwise properly indexed for
    its OTHER access patterns — allowed only for the one read that bypasses the index,
    never widened to the whole table, so a future per-key read added against the same
    table stays held to using the real index."""

    protocol: type
    method: str
    table: str
    row_bound: int
    reason: str


# Points at schema.py's own "Deliberately unindexed (issue #520)" comment near `asks` (`bzh:one-prose-home`).
_SCHEMA_520_REASON = (
    "schema.py's own 'Deliberately unindexed (issue #520)' comment beside `asks`: "
    "near-empty by design, so a scan beats an index's upkeep."
)

# Every table below is one runner's own local, per-environment store — small for that environment's own lifetime.
_ENV_SCOPED_HISTORY_REASON = (
    "one feature environment's own local runner store (a fresh one per `winter ws init`, "
    "never shared fleet-wide) — even this table's full history across that one "
    "environment's development lifetime stays well under the threshold."
)

# Every table below records a rare, human/operator-triggered event — sparse by the event's own nature.
_RARE_OPERATOR_EVENT_REASON = (
    "a rare human/operator-triggered event (pause, takeover, requeue, restart, or a "
    "hub-resolved escalation) — sparse by the event's own nature, not by retention."
)

RUNNER_ALLOWED_SCANS: list[TableWideAllowance | MethodScopedAllowance] = [
    # --- the #520-named six (schema.py, beside `asks`) --------------------------------
    TableWideAllowance("asks", 50, _SCHEMA_520_REASON),
    TableWideAllowance("park_facts", 50, _SCHEMA_520_REASON),
    TableWideAllowance("park_resumes", 50, _SCHEMA_520_REASON),
    TableWideAllowance("check_results", 50, _SCHEMA_520_REASON),
    TableWideAllowance("checks_ran", 50, _SCHEMA_520_REASON),
    TableWideAllowance("in_flight_elicitations", 50, _SCHEMA_520_REASON),
    # --- one environment's own local history ------------------------------------------
    TableWideAllowance("leases", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("lease_context", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("lease_closures", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("lease_spawns", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("usage_facts", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("git_commit_declarations", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("session_preamble_facts", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("session_ends", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("env_bindings", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("nudge_facts", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("selftest_results", 200, _ENV_SCOPED_HISTORY_REASON),
    # --- rare operator-triggered events -------------------------------------------------
    TableWideAllowance("resume_intents", 50, _RARE_OPERATOR_EVENT_REASON),
    TableWideAllowance("resume_clears", 50, _RARE_OPERATOR_EVENT_REASON),
    TableWideAllowance("takeovers", 50, _RARE_OPERATOR_EVENT_REASON),
    TableWideAllowance("takeover_ends", 50, _RARE_OPERATOR_EVENT_REASON),
    TableWideAllowance("requeues", 50, _RARE_OPERATOR_EVENT_REASON),
    TableWideAllowance("escalation_closures", 50, _RARE_OPERATOR_EVENT_REASON),
    TableWideAllowance("pause_parks", 50, _RARE_OPERATOR_EVENT_REASON),
    TableWideAllowance("pause_park_resumes", 50, _RARE_OPERATOR_EVENT_REASON),
    TableWideAllowance("local_pause_facts", 50, _RARE_OPERATOR_EVENT_REASON),
    # --- the transcript lane's own pending-delivery buffer ------------------------------
    TableWideAllowance(
        "transcript_outbound_buffer",
        100,
        "the transcript pump's own store-and-forward buffer (D3) — no dedicated index "
        "(unlike `outbound_buffer`'s `ix_outbound_buffer_acked_at_seq`), but the drain "
        "keeps it near-empty in healthy operation, the same smallness the #520 six lean on.",
    ),
    # --- deliberate whole-table reads of otherwise-indexed tables -----------------------
    MethodScopedAllowance(
        IReadOutboundRepository,
        "recent_outbound",
        "outbound_buffer",
        100,
        "bounded by IWriteOutboundRepository.prune_outbound's 7-day retention window, "
        "unlike the OTHER outbound_buffer reads (pending_outbound and friends), which "
        "filter on acked_at and so do hit ix_outbound_buffer_acked_at_seq — this one "
        "deliberately reads the newest N regardless of ack state, which that index does "
        "not cover.",
    ),
    MethodScopedAllowance(
        IReadTranscriptLedgerRepository,
        "transcript_backfill_leases",
        "transcript_segments",
        200,
        "the backfill's own correlated EXISTS keys on session_id, not the chunk_id/"
        "stamped_at/segment_id ix_transcript_segments_chunk_id_stamped_at_segment_id "
        "every OTHER transcript_segments read hits — a one-time sweep (blizzard#250), "
        "bounded by one environment's own session history.",
    ),
]

# The hub's store is shared fleet-wide, unlike the runner's own per-environment store (module docstring above) —
# populated empirically: each entry below names the specific, bounded reason its scan is genuinely small or rare.

# ChunkFactsStore.load_all_facts/load_all_statuses (#374) and their queue-position siblings read this way by design.
_FLEET_SNAPSHOT_REASON = (
    "ChunkFactsStore.load_all_facts/load_all_statuses (issue #374) and their "
    "ChunkQueueStore siblings read one bounded query per fact table across the WHOLE "
    "store by design — the method's entire point is a fleet-wide snapshot, not a "
    "per-chunk lookup, so no chunk_id index applies."
)

# chunk_deleted/chunk_grouped carry no chunk_id index — every per-chunk tombstone check scans them.
_TOMBSTONE_NO_KEY_INDEX_REASON = (
    "chunk_deleted/chunk_grouped carry no chunk_id index (only their own activity-feed "
    "deleted_at/id and grouped_at/id pairs) — every per-chunk tombstone check scans them, "
    "on top of being read fleet-wide by the #374 snapshot methods above; deletions/merges "
    "are rare relative to the fleet's own chunk volume."
)

# ChunkDecisionsStore._not_closed_clause correlates a NOT EXISTS on decision_id, unindexed on all four tables.
_DECISION_CLOSURE_REASON = (
    "ChunkDecisionsStore._not_closed_clause correlates a NOT EXISTS against this table on "
    "decision_id, a column it carries no index on (its own indexes serve chunk_id/epoch or "
    "recorded_at/id reads instead) — on top of being read fleet-wide by the #374 snapshot "
    "methods above."
)

# Every table below holds an operator-created fleet entity, bounded by entity count, not chunk/work-item volume.
_FLEET_CONFIG_REASON = (
    "an operator-created fleet entity (registered runner, scope, routine, graph, hub "
    "user) or an auth event about one — bounded by how many of those entities exist "
    "across the whole fleet's lifetime, not by chunk/work-item volume."
)

# A declared singleton (superuser_bootstrap) or the fleet-wide hub-exec serialization slot (#65, hub_exec_slot).
_SINGLETON_REASON = (
    "a declared singleton (superuser_bootstrap) or the fleet-wide single hub-exec "
    "serialization slot (#65, hub_exec_slot) — at most one live row plus a short "
    "release history."
)

HUB_ALLOWED_SCANS: list[TableWideAllowance | MethodScopedAllowance] = [
    # --- fleet-wide-by-design snapshot reads (issue #374) ------------------------------
    TableWideAllowance("chunk_bounces", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("chunk_completed", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("chunk_pause_facts", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("chunk_stopped", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("chunks", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("decisions", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("delivery_pr_closed", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("delivery_pr_opened", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("delivery_repo_landed", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("hub_node_poll", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("lease_facts", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("questions", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("requeues", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("route_created", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("route_released", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("route_token_minted", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("usage_facts", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("chunk_work_refs", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("chunk_promoted", 200, _FLEET_SNAPSHOT_REASON),
    TableWideAllowance("queue_positions", 200, _FLEET_SNAPSHOT_REASON),
    # --- tombstones with no chunk_id index ----------------------------------------------
    TableWideAllowance("chunk_deleted", 200, _TOMBSTONE_NO_KEY_INDEX_REASON),
    TableWideAllowance("chunk_grouped", 200, _TOMBSTONE_NO_KEY_INDEX_REASON),
    # --- decision-closure correlated NOT EXISTS on an unindexed decision_id ------------
    TableWideAllowance("transitions", 200, _DECISION_CLOSURE_REASON),
    TableWideAllowance("chunk_migrations", 200, _DECISION_CLOSURE_REASON),
    TableWideAllowance("escalations", 200, _DECISION_CLOSURE_REASON),
    TableWideAllowance("chunk_restarts", 200, _DECISION_CLOSURE_REASON),
    # --- operator-created fleet entities, bounded by entity count not chunk volume -----
    TableWideAllowance("runner_registrations", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("runner_pause_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("runner_local_pause_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("scopes", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("scope_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("routines", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("graphs", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("graph_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("graph_policy_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("users", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("auth_facts", 200, _FLEET_CONFIG_REASON),
    # --- singletons/near-singletons ------------------------------------------------------
    TableWideAllowance("superuser_bootstrap", 50, _SINGLETON_REASON),
    TableWideAllowance("hub_exec_slot", 50, _SINGLETON_REASON),
    # --- one-off unindexed columns on otherwise-small or otherwise-indexed tables ------
    TableWideAllowance(
        "route_environments",
        200,
        "route_of_conn (chunk_rows.py) reads this table filtered on route_id, a column "
        "it carries no index on at all (its only column besides the surrogate id and the "
        "opaque environment_id) — a route rarely names more than one or two "
        "environments, so this stays small alongside route_created/route_released.",
    ),
    TableWideAllowance(
        "answer_deliveries",
        200,
        "board-detail-only per src/blizzard/hub/store/schema.py's own comment beside "
        "answer_deliveries, with no index at all — one row per answered question, a "
        "small fraction of the fleet's own question volume.",
    ),
    TableWideAllowance(
        "event_log",
        200,
        "ChunkEventsStore.list_events's own default call (no severity/runner_id/"
        "chunk_id/since filter) is a deliberate unfiltered browse of the operational "
        "event log — ix_event_log_recorded_at serves the filtered/paged callers, not "
        "this one.",
    ),
    TableWideAllowance(
        "chunk_dependencies",
        200,
        "ChunkDependenciesStore.list_standing_edges filters on released_at IS NULL, a "
        "column with no index (ix_chunk_dependencies_dependent_chunk_id/"
        "prerequisite_chunk_id serve the OTHER reads) — standing dependency edges are a "
        "declared-governance feature, bounded by how many edges operators/routines "
        "declare, not by raw chunk volume.",
    ),
    TableWideAllowance(
        "garden_proposals",
        200,
        "GardenProposalStore.list_all reads every proposal unfiltered, ordered newest "
        "first — proposals are minted at gardening-routine pace (one per accepted "
        "remediation), a materially smaller volume than the chunk fleet itself.",
    ),
    TableWideAllowance(
        "transcript_event_derivations",
        200,
        "TranscriptEventStore.candidacy/derivation_markers filter on extractor_version "
        "alone, the trailing half of this table's (segment_id, extractor_version) "
        "primary key — no index leads with extractor_version. Both are the event "
        "derivation sweep's own internal bookkeeping reads (event_derivation.sweep()), "
        "not a per-request hot path.",
    ),
    # --- deliberate whole-table read of an otherwise-indexed table ---------------------
    MethodScopedAllowance(
        IReadFindingRepository,
        "has_delivery_for_proposal",
        "finding_facts",
        200,
        "filters on proposal_id, a column ix_finding_facts_finding_id_id and "
        "ix_finding_facts_finding_set_id don't cover — finding_facts's OTHER reads (by "
        "finding_id or finding_set_id) do hit an index; this one deliberately checks a "
        "rare, one-per-proposal delivery fact those indexes don't serve.",
    ),
]
