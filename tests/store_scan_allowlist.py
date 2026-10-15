"""Store-read-index gate allow-list.

``ROW_THRESHOLD`` bounds what a "deliberately unindexed" table's declared row bound may claim — an entry above it
fails the gate's own hygiene check, not a measured runtime fact (sqlite's plan is row-count-independent without
``ANALYZE``). ``RUNNER_ALLOWED_SCANS`` leans on the runner's own store being per-environment (small by lifetime);
``HUB_ALLOWED_SCANS`` cannot — the hub's store is shared fleet-wide, so each entry names its own bounded reason."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.auth.hub_admins import IReadHubAdminRepository
from blizzard.hub.auth.invitations import IReadInvitationRepository
from blizzard.hub.auth.memberships import IReadMembershipRepository
from blizzard.hub.domain.chunk.ports.decisions import IReadChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.delivery import IReadChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.dependencies import IReadChunkDependenciesRepository
from blizzard.hub.domain.chunk.ports.escalations import IReadChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.events import IReadChunkEventsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.questions import IReadChunkQuestionsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository, IReadWorkRefsCorpus
from blizzard.hub.domain.config.carry_over import IReadConfigImports
from blizzard.hub.domain.config.changes import IReadConfigChanges
from blizzard.hub.domain.garden.findings.model import IReadFindingRepository
from blizzard.hub.domain.garden.findings.trend import IReadGardenTrendRepository
from blizzard.hub.domain.garden.proposals.model import IReadGardenProposalRepository
from blizzard.hub.domain.observability.analytics.derivation import IReadDerivationCorpus
from blizzard.hub.domain.observability.analytics.events import IReadTranscriptEvents
from blizzard.hub.domain.observability.analytics.operational import IReadOperationalAnalytics
from blizzard.hub.domain.observability.analytics.queries import IReadAnalyticsEventQueries
from blizzard.hub.domain.observability.forge_status import IReadAnnotatedSources
from blizzard.hub.domain.observability.hub_events import IReadHubEventLog
from blizzard.hub.domain.observability.tracing.repository import IReadTraceStatus, IReadTraceSteps
from blizzard.hub.domain.tenancy.administration import IReadTenantDirectory
from blizzard.runner.tracing.repository import IReadLeaseTraceCursor
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


@dataclass(frozen=True)
class IndexWalkAllowance:
    """A read that walks a table through a named index by design: a search on the tenant prefix alone
    (``SEARCH t USING INDEX ix (tenant_id=?)``) or an ordered walk of the whole index (``SCAN t USING INDEX ix``).
    Allowed only for the one read and only for those plans — never a bare scan or an automatic index — and it
    declares no row bound: the walk is the read's own shape (a snapshot, an aggregate, a newest-row probe), not a
    claim that the table stays small, so a per-id read added against the same tables stays held to a real lookup."""

    protocol: type
    method: str
    tables: tuple[str, ...]
    reason: str


def walks_a_named_index(detail: str) -> bool:
    """Whether a plan row reads its table through a real named index — the only offense an
    :class:`IndexWalkAllowance` covers."""
    return " USING " in detail and "AUTOMATIC" not in detail


Allowance = TableWideAllowance | MethodScopedAllowance | IndexWalkAllowance


# --- why a read walks a named index by design ---------------------------------------------------------------------

# A board, status, or sweep read whose point is every live chunk of the tenant (or, hub-scoped, of every open tenant).
_TENANT_SNAPSHOT_REASON = (
    "a whole-tenant snapshot by design (the board, `hub status`, a sweep's working set): it reads every live chunk "
    "and the facts that decide its state, decision closure included, in one bounded query per table rather than one "
    "lookup per chunk — no id narrows a read whose subject is the whole tenant."
)

# A change signature: count/max over the same tables the snapshot reads, polled to skip an unchanged rebuild.
_SIGNATURE_REASON = (
    "a change signature — counts and maxima over every table the work-refs snapshot reads, polled so an unchanged "
    "snapshot is not rebuilt; an aggregate over the whole tenant (hub-scoped: every open tenant) has no id to seek."
)

# ChunkDecisionsStore._not_closed_clause correlates a NOT EXISTS on decision_id, which no index leads with.
_DECISION_CLOSURE_REASON = (
    "ChunkDecisionsStore._not_closed_clause correlates a NOT EXISTS against this table on decision_id, which no index "
    "on it leads with after tenant_id (its indexes serve chunk_id/epoch and recorded_at reads), so each probed "
    "decision walks the tenant's rows here; decisions are gate-paced, a small share of chunk traffic."
)

# An analytics aggregate whose subject is the tenant's whole history or a time window.
_ANALYTICS_REASON = (
    "an analytics aggregate over the tenant's whole history, or a time window no index leads with — the surface's "
    "answer is a tenant-wide total, so the read is a walk by design."
)

# The transcript-event derivation sweep's candidacy and markers reads.
_DERIVATION_SWEEP_REASON = (
    "the transcript-event derivation sweep reads every segment and derivation marker of the tenant (hub-scoped: of "
    "every open tenant) to find what is not yet derived — a corpus pass, not a per-chunk lookup."
)

# `ORDER BY ... DESC LIMIT 1` along the index's own order — the plan text cannot show the LIMIT.
_NEWEST_ROW_REASON = (
    "a newest-row probe — `ORDER BY ... DESC LIMIT 1` along the walked index's own order, so it stops at the first "
    "matching row; the plan text cannot show the LIMIT."
)

# A keyset-paged or newest-first listing, capped by its page size.
_PAGED_LISTING_REASON = (
    "a paged listing walking the index in its own order and stopping at the page's `limit`; the optional filters "
    "narrow inside the walk."
)

# The runner's transcript pump reads the open segments of its own local store.
_OPEN_SEGMENTS_REASON = (
    "the transcript pump's open-segment read walks one environment's local segments in key order to find the ones "
    "still open — bounded by that environment's own session history."
)

# The legacy-config import runs once per hub, offline, by an operator — never on a serving path.
_ONE_SHOT_IMPORT_REASON = (
    "read only by `blizzard hub config import-legacy`, an offline verb an operator runs once per hub."
)

# Points at the "Deliberately unindexed" comment near `asks` in src/blizzard/runner/store/schema.py (`bzh:one-prose-home`).
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

RUNNER_ALLOWED_SCANS: list[Allowance] = [
    # --- the named six (src/blizzard/runner/store/schema.py, beside `asks`) --------------------------------
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
    TableWideAllowance("overload_facts", 200, _ENV_SCOPED_HISTORY_REASON),
    TableWideAllowance("overload_resets", 200, _ENV_SCOPED_HISTORY_REASON),
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
    # --- a singleton --------------------------------------------------------------------
    TableWideAllowance(
        "host_identity",
        1,
        "the host's registration row — a singleton the registered host records once, so an existence probe reads at most one row.",
    ),
    TableWideAllowance(
        "host_outbound_buffer",
        100,
        "the host lane's own store-and-forward buffer — the drain keeps it near-empty in healthy operation, the "
        "same smallness the transcript lane's buffer leans on.",
    ),
    TableWideAllowance(
        "host_high_water_seen",
        1,
        "the host lane's high-water singleton — one row, so a probe reads at most one.",
    ),
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
        IReadTranscriptLedgerRepository,
        "transcript_backfill_leases",
        "transcript_segments",
        200,
        "the backfill's own correlated EXISTS keys on session_id, not the chunk_id/"
        "stamped_at/segment_id ix_transcript_segments_chunk_id_stamped_at_segment_id "
        "every OTHER transcript_segments read hits — a one-time sweep (blizzard#250), "
        "bounded by one environment's own session history.",
    ),  # --- named-index walks by design ----------------------------------------------------
    IndexWalkAllowance(IReadLeaseTraceCursor, "newest_export_cursor", ("trace_cursor",), _NEWEST_ROW_REASON),
    IndexWalkAllowance(IReadLeaseTraceCursor, "newest_trace_cursor", ("trace_cursor",), _NEWEST_ROW_REASON),
    IndexWalkAllowance(IReadLeaseTraceCursor, "newest_trace_latch", ("trace_export_latch",), _NEWEST_ROW_REASON),
    IndexWalkAllowance(
        IReadTranscriptLedgerRepository, "open_transcript_segments", ("transcript_segments",), _OPEN_SEGMENTS_REASON
    ),
    IndexWalkAllowance(
        IReadTranscriptLedgerRepository,
        "open_transcript_segments_for_lease",
        ("transcript_segments",),
        _OPEN_SEGMENTS_REASON,
    ),
]

# The hub's store is shared fleet-wide, unlike the runner's own per-environment store (module docstring above) —
# populated empirically: each entry below names the specific, bounded reason its scan is genuinely small or rare.

# ChunkFactsStore.load_all_facts/load_live_statuses (#374) read this way by design.
_FLEET_SNAPSHOT_REASON = (
    "ChunkFactsStore.load_all_facts/load_live_statuses (issue #374) read one "
    "bounded query per fact table across the WHOLE "
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

# Every table below holds an operator-created fleet entity, bounded by entity count, not chunk/work-item volume.
_FLEET_CONFIG_REASON = (
    "an operator-created fleet entity (registered runner, scope, secret, work source, routine, graph, hub "
    "user) or an auth event about one — bounded by how many of those entities exist "
    "across the whole fleet's lifetime, not by chunk/work-item volume."
)

# A declared singleton (superuser_bootstrap).
_SINGLETON_REASON = "a declared singleton (superuser_bootstrap) — at most one live row plus a short history."

# Each tenant's hub-exec serialization slot (hub_exec_slot).
_HUB_EXEC_SLOT_REASON = (
    "one tenant's hub-exec serialization slot: the tenant-scoped read sees at most one live row "
    "plus that tenant's release history, never another tenant's slots."
)

# The hub's tenant deletion facts (tenant_deletions), read for the deletions still underway.
_TENANT_DELETIONS_REASON = (
    "one fact per tenant the hub ever deleted, read whole by the tenant-teardown sweep for the deletions still "
    "underway — bounded by the tenants hub administrators created, never by a tenant's own volume."
)

HUB_ALLOWED_SCANS: list[Allowance] = [
    # --- the one-shot legacy-config import ----------------------------------------------
    TableWideAllowance("config_import_facts", 50, _RARE_OPERATOR_EVENT_REASON),
    # --- the derivation sweep's corpus read of every open tenant's markers ----------
    MethodScopedAllowance(
        IReadDerivationCorpus,
        "candidacy",
        "transcript_event_derivations",
        200,
        "the derivation sweep's whole-corpus read: every open tenant's markers for one extractor_version, read "
        "together; no tenant-led key narrows a read across tenants, and none leads with extractor_version.",
    ),
    # --- fleet-wide-by-design snapshot reads ------------------------------
    TableWideAllowance("chunk_work_refs", 200, _FLEET_SNAPSHOT_REASON),
    # --- tombstones with no chunk_id index ----------------------------------------------
    TableWideAllowance("chunk_deleted", 200, _TOMBSTONE_NO_KEY_INDEX_REASON),
    TableWideAllowance("chunk_grouped", 200, _TOMBSTONE_NO_KEY_INDEX_REASON),
    # --- operator-created fleet entities, bounded by entity count not chunk volume -----
    TableWideAllowance("users", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("auth_facts", 200, _FLEET_CONFIG_REASON),
    # --- singletons/near-singletons ------------------------------------------------------
    TableWideAllowance("superuser_bootstrap", 50, _SINGLETON_REASON),
    TableWideAllowance("hub_exec_slot", 50, _HUB_EXEC_SLOT_REASON),
    TableWideAllowance("tenant_deletions", 200, _TENANT_DELETIONS_REASON),
    # --- operator-created fleet entities and their lifecycle facts --------------------
    TableWideAllowance("graph_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("graph_policy_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("graphs", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("host_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("hosts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("identities", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("projects", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("repositories", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("repository_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("routine_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("routine_scopes", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("routines", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("runner_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("runner_local_pause_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("runner_pause_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("runner_registrations", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("scope_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("scopes", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("secret_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("secrets", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("work_source_lifecycle_facts", 200, _FLEET_CONFIG_REASON),
    TableWideAllowance("work_sources", 200, _FLEET_CONFIG_REASON),
    MethodScopedAllowance(
        IReadInvitationRepository,
        "list",
        "invitations",
        200,
        "every invitation one tenant has issued, read whole on its tenant key — the tenant's invitation list is what "
        "`invite list` shows, bounded by the invitations its admins hand out; a lookup by id or token uses its own key.",
    ),
    # --- named-index walks by design ----------------------------------------------------
    IndexWalkAllowance(IReadAnalyticsEventQueries, "counts_by_agent_type", ("transcript_events",), _ANALYTICS_REASON),
    IndexWalkAllowance(IReadAnalyticsEventQueries, "counts_by_node", ("transcript_events",), _ANALYTICS_REASON),
    IndexWalkAllowance(
        IReadMembershipRepository,
        "members",
        ("membership_facts",),
        "every membership fact one tenant holds, each reduced to its user's newest — the tenant's member list, "
        "bounded by the people it grants.",
    ),
    IndexWalkAllowance(
        IReadMembershipRepository,
        "admin_count",
        ("membership_facts",),
        "every membership fact one tenant holds, counted where its user's newest grants admin — an aggregate "
        "over the tenant's member list.",
    ),
    IndexWalkAllowance(
        IReadMembershipRepository,
        "only_tenant",
        ("tenants",),
        "reads at most two tenants to tell whether the hub holds exactly one — a LIMIT 2 read of the key index's "
        "first entries, never a walk of it, whatever the tenant count.",
    ),
    IndexWalkAllowance(
        IReadTenantDirectory,
        "list_tenants",
        ("tenants", "tenant_deletions"),
        "every tenant the hub holds, walked down its key in creation order, each probed once for a deletion fact by "
        "the tenant's own id — the hub administrator's list of tenants, bounded by the tenants they created.",
    ),
    IndexWalkAllowance(
        IReadHubEventLog,
        "recent",
        ("hub_event_log",),
        "the newest page of the hub's own event log, walked down ix_hub_event_log_recorded_at from its newest end "
        "and stopped at the page's limit — a newest-rows read, never a walk of the whole log.",
    ),
    IndexWalkAllowance(
        IReadHubAdminRepository,
        "holders",
        ("hub_admin_facts",),
        "every hub-administrator fact, each reduced to its user's newest — the hub's administrators, read whole "
        "and bounded by the grants and revocations of a role a handful of people hold.",
    ),
    IndexWalkAllowance(
        IReadAnnotatedSources,
        "annotated_sources",
        ("forge_annotation_facts",),
        "every source this tenant counts as annotated, read whole by the departure sweep — one row per annotated "
        "work-source item, bounded by the open work the tenant's forges carry.",
    ),
    IndexWalkAllowance(
        IReadChunkDecisionsRepository,
        "decision_for_chunk",
        ("chunk_migrations", "chunk_restarts", "escalations", "transitions"),
        _DECISION_CLOSURE_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkDecisionsRepository,
        "find_decision",
        ("chunk_migrations", "chunk_restarts", "escalations", "transitions"),
        _DECISION_CLOSURE_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkDecisionsRepository,
        "get_decision",
        ("chunk_migrations", "chunk_restarts", "escalations", "transitions"),
        _DECISION_CLOSURE_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkDecisionsRepository,
        "list_open_decisions",
        ("chunk_migrations", "chunk_restarts", "decisions", "escalations", "transitions"),
        _TENANT_SNAPSHOT_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkDecisionsRepository,
        "live_decisions_for",
        ("chunk_migrations", "chunk_restarts", "escalations", "transitions"),
        _DECISION_CLOSURE_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkDeliveryRepository, "count_landed_since", ("delivery_repo_landed",), _ANALYTICS_REASON
    ),
    IndexWalkAllowance(
        IReadChunkDeliveryRepository,
        "pending_close_intents",
        ("close_intent_attempts", "close_intents"),
        "every pending close intent of the tenant with its attempts, aggregated in one grouped pass over the "
        "tenant's attempts rather than one query per intent — the delivery sweep reads every pending intent anyway.",
    ),
    IndexWalkAllowance(
        IReadChunkDependenciesRepository, "list_standing_edges", ("chunk_dependencies",), _TENANT_SNAPSHOT_REASON
    ),
    IndexWalkAllowance(
        IReadChunkDependenciesRepository,
        "standing_edges_for",
        ("chunk_dependencies",),
        "an OR of two equalities (dependent or prerequisite) that SQLite plans as one walk of the tenant's "
        "dependency edges; standing edges are declared by operators and routines, not minted per chunk.",
    ),
    IndexWalkAllowance(IReadChunkEscalationsRepository, "list_open_escalations", ("chunks",), _TENANT_SNAPSHOT_REASON),
    IndexWalkAllowance(IReadChunkEventsRepository, "list_events", ("event_log",), _PAGED_LISTING_REASON),
    IndexWalkAllowance(
        IReadChunkFactsRepository,
        "load_all_facts",
        (
            "chunk_bounces",
            "chunk_completed",
            "chunk_migrations",
            "chunk_pause_facts",
            "chunk_promoted",
            "chunk_restarts",
            "chunk_stopped",
            "chunks",
            "decision_resolutions",
            "decisions",
            "delivery_landed",
            "delivery_repo_landed",
            "epoch_owners",
            "escalations",
            "hub_node_poll",
            "lease_facts",
            "questions",
            "requeues",
            "route_created",
            "route_released",
            "route_token_minted",
            "transitions",
            "usage_facts",
        ),
        _TENANT_SNAPSHOT_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkFactsRepository,
        "load_live_statuses",
        (
            "chunk_migrations",
            "chunk_restarts",
            "chunks",
            "decision_resolutions",
            "decisions",
            "escalations",
            "transitions",
        ),
        _TENANT_SNAPSHOT_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkFactsRepository,
        "status_counts",
        (
            "chunk_migrations",
            "chunk_restarts",
            "chunks",
            "decision_resolutions",
            "decisions",
            "escalations",
            "transitions",
        ),
        _TENANT_SNAPSHOT_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkFactsRepository,
        "done_since_count",
        (
            "chunk_completed",
            "chunk_migrations",
            "chunk_restarts",
            "chunks",
            "decision_resolutions",
            "decisions",
            "escalations",
            "transitions",
        ),
        _TENANT_SNAPSHOT_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkQuestionsRepository,
        "get_question",
        ("answer_deliveries",),
        "the newest delivery per question is a grouped subquery over the tenant's answer deliveries — one row per "
        "delivered answer, bounded by the questions the tenant's chunks ask.",
    ),
    IndexWalkAllowance(
        IReadChunkQuestionsRepository,
        "list_open_questions",
        ("answer_deliveries", "question_answers", "questions"),
        _TENANT_SNAPSHOT_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkQuestionsRepository, "load_questions", ("answer_deliveries", "questions"), _TENANT_SNAPSHOT_REASON
    ),
    IndexWalkAllowance(IReadChunkRecordRepository, "list_all", ("chunks",), _TENANT_SNAPSHOT_REASON),
    IndexWalkAllowance(
        IReadChunkRecordRepository,
        "list_not_ready",
        (
            "chunk_migrations",
            "chunk_restarts",
            "chunks",
            "decision_resolutions",
            "decisions",
            "escalations",
            "transitions",
        ),
        _TENANT_SNAPSHOT_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkRecordRepository,
        "newest_done",
        ("chunk_completed", "chunks"),
        "the board's done floor ranks every `done` chunk of the scope by a derived completion instant no index "
        "orders, and returns only the newest `BOARD_DONE_FLOOR` ids — a fleet-sized walk by design, one statement.",
    ),
    IndexWalkAllowance(IReadChunkRecordRepository, "list_page", ("chunks",), _PAGED_LISTING_REASON),
    IndexWalkAllowance(
        IReadChunkRecordRepository,
        "list_ready",
        (
            "chunk_migrations",
            "chunk_restarts",
            "chunks",
            "decision_resolutions",
            "decisions",
            "escalations",
            "transitions",
        ),
        _TENANT_SNAPSHOT_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkRouteRepository, "load_all_routes", ("route_created", "route_released"), _TENANT_SNAPSHOT_REASON
    ),
    IndexWalkAllowance(IReadChunkWorkRefsRepository, "live_holders", ("chunks",), _TENANT_SNAPSHOT_REASON),
    IndexWalkAllowance(
        IReadChunkWorkRefsRepository,
        "live_work_refs",
        (
            "chunk_migrations",
            "chunk_restarts",
            "chunks",
            "decision_resolutions",
            "decisions",
            "escalations",
            "transitions",
        ),
        _TENANT_SNAPSHOT_REASON,
    ),
    IndexWalkAllowance(
        IReadChunkWorkRefsRepository,
        "live_work_refs_signature",
        (
            "chunk_completed",
            "chunk_migrations",
            "chunk_pause_facts",
            "chunk_promoted",
            "chunk_restarts",
            "chunk_stopped",
            "chunks",
            "decision_resolutions",
            "decisions",
            "escalations",
            "lease_facts",
            "question_answers",
            "questions",
            "requeues",
            "route_created",
            "route_released",
            "transitions",
        ),
        _SIGNATURE_REASON,
    ),
    IndexWalkAllowance(IReadConfigChanges, "page", ("config_changes",), _PAGED_LISTING_REASON),
    IndexWalkAllowance(
        IReadConfigChanges,
        "count",
        ("config_changes",),
        "a scalar count over the tenant's change rows, which grow only by configuration writes — one covering-index "
        "walk with no row loaded.",
    ),
    IndexWalkAllowance(
        IReadConfigImports,
        "commit_coordinates",
        ("artifacts",),
        _ONE_SHOT_IMPORT_REASON,
    ),
    IndexWalkAllowance(
        IReadDerivationCorpus, "derivation_signature", ("chunks", "transcript_segments"), _DERIVATION_SWEEP_REASON
    ),
    IndexWalkAllowance(
        IReadDerivationCorpus, "derived_segment_ids", ("transcript_event_derivations",), _DERIVATION_SWEEP_REASON
    ),
    IndexWalkAllowance(IReadFindingRepository, "list_page", ("findings",), _PAGED_LISTING_REASON),
    IndexWalkAllowance(IReadGardenProposalRepository, "counts_by_class", ("garden_proposals",), _ANALYTICS_REASON),
    IndexWalkAllowance(
        IReadGardenProposalRepository,
        "list_all",
        ("garden_proposals",),
        _PAGED_LISTING_REASON,
    ),
    IndexWalkAllowance(
        IReadGardenProposalRepository,
        "list_page",
        ("garden_proposals",),
        _PAGED_LISTING_REASON,
    ),
    IndexWalkAllowance(IReadGardenTrendRepository, "facts_for_trend", ("finding_facts",), _ANALYTICS_REASON),
    IndexWalkAllowance(IReadOperationalAnalytics, "durations_by_graph", ("transitions",), _ANALYTICS_REASON),
    IndexWalkAllowance(IReadOperationalAnalytics, "durations_by_node", ("transitions",), _ANALYTICS_REASON),
    IndexWalkAllowance(
        IReadOperationalAnalytics, "outcomes_by_node", ("chunks", "lease_facts", "transitions"), _ANALYTICS_REASON
    ),
    IndexWalkAllowance(IReadOperationalAnalytics, "spend_by_chunk", ("usage_facts",), _ANALYTICS_REASON),
    IndexWalkAllowance(IReadOperationalAnalytics, "spend_by_graph", ("usage_facts",), _ANALYTICS_REASON),
    IndexWalkAllowance(IReadOperationalAnalytics, "spend_by_node", ("usage_facts",), _ANALYTICS_REASON),
    IndexWalkAllowance(IReadTraceStatus, "newest_export_cursor", ("trace_cursor",), _NEWEST_ROW_REASON),
    IndexWalkAllowance(IReadTraceSteps, "newest_cursor", ("trace_cursor",), _NEWEST_ROW_REASON),
    IndexWalkAllowance(
        IReadTranscriptEvents,
        "candidacy",
        ("chunks", "transcript_event_derivations", "transcript_segments"),
        _DERIVATION_SWEEP_REASON,
    ),
    IndexWalkAllowance(
        IReadTranscriptEvents, "derivation_markers", ("transcript_event_derivations",), _DERIVATION_SWEEP_REASON
    ),
    IndexWalkAllowance(
        IReadTranscriptEvents, "derivation_signature", ("chunks", "transcript_segments"), _DERIVATION_SWEEP_REASON
    ),
    IndexWalkAllowance(
        IReadTranscriptEvents, "derived_segment_ids", ("transcript_event_derivations",), _DERIVATION_SWEEP_REASON
    ),
    IndexWalkAllowance(
        IReadTranscriptEvents, "visible_segment_ids", ("chunks", "transcript_segments"), _DERIVATION_SWEEP_REASON
    ),
    IndexWalkAllowance(
        IReadWorkRefsCorpus,
        "live_work_refs_signature",
        (
            "chunk_completed",
            "chunk_migrations",
            "chunk_pause_facts",
            "chunk_promoted",
            "chunk_restarts",
            "chunk_stopped",
            "chunks",
            "decision_resolutions",
            "decisions",
            "escalations",
            "lease_facts",
            "question_answers",
            "questions",
            "requeues",
            "route_created",
            "route_released",
            "tenant_deletions",
            "transitions",
        ),
        _SIGNATURE_REASON,
    ),
]
