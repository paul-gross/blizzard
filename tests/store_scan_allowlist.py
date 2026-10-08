"""Store-read-index gate allow-list.

``ROW_THRESHOLD`` bounds what a "deliberately unindexed" table's declared row bound may claim — an entry above it
fails the gate's own hygiene check, not a measured runtime fact (sqlite's plan is row-count-independent without
``ANALYZE``). ``RUNNER_ALLOWED_SCANS`` leans on the runner's own store being per-environment (small by lifetime);
``HUB_ALLOWED_SCANS`` cannot — the hub's store is shared fleet-wide, so each entry names its own bounded reason."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.domain.observability.analytics.derivation import IReadDerivationCorpus
from blizzard.runner.hub.outbound_buffer import IReadOutboundRepository
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

# Points at src/blizzard/runner/store/schema.py's "A singleton row" comments beside `hub_control`, `runner_identity`.
_RUNNER_SINGLETON_REASON = (
    "schema.py's own 'A singleton row' comments beside `hub_control` and `runner_identity`: "
    "every write replaces the one row in place, so a scan reads at most one row."
)

RUNNER_ALLOWED_SCANS: list[TableWideAllowance | MethodScopedAllowance] = [
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
    # --- singletons ---------------------------------------------------------------------
    TableWideAllowance("hub_control", 1, _RUNNER_SINGLETON_REASON),
    TableWideAllowance("runner_identity", 1, _RUNNER_SINGLETON_REASON),
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

# A declared singleton (superuser_bootstrap) or the fleet-wide hub-exec serialization slot (#65, hub_exec_slot).
_SINGLETON_REASON = (
    "a declared singleton (superuser_bootstrap) or the fleet-wide single hub-exec "
    "serialization slot (#65, hub_exec_slot) — at most one live row plus a short "
    "release history."
)

HUB_ALLOWED_SCANS: list[TableWideAllowance | MethodScopedAllowance] = [
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
    TableWideAllowance("hub_exec_slot", 50, _SINGLETON_REASON),
]
