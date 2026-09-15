"""Store-read-index gate allow-list (blizzard#525, Phase 2).

``ROW_THRESHOLD`` bounds what a "deliberately unindexed" table's declared row bound may
claim — an allow-list entry above it fails the gate's own hygiene check
(``tests/test_store_read_index_gate.py``), not a measured runtime fact (sqlite's query
plan is row-count-independent without ``ANALYZE``, and this repo's
``create_engine_from_url`` never runs it).

The runner's own local sqlite store is per-feature-environment (``winter ws init``
provisions a fresh one; the workspace's own ``AGENTS.md`` owns that lifecycle) — its
tables hold one environment's own development history, not the whole fleet's, which is
why most of them stay small enough to allow table-wide below. Contrast the hub's own
store (Phase 3), which is shared fleet-wide and cannot lean on the same argument.

Split by store at the module level (``RUNNER_ALLOWED_SCANS``) rather than one generic
structure, so Phase 3 can add a parallel ``HUB_ALLOWED_SCANS`` here without reshaping
what the runner half already owns.
"""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.runner.domain.outbound import IReadOutboundRepository
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository

#: An allow-list entry's declared row bound must never exceed this — the ceiling on what
#: "small enough that a scan beats an index's upkeep" may claim.
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
    never widened to the whole table (Decision 6), so a future per-key read added
    against the same table stays held to using the real index."""

    protocol: type
    method: str
    table: str
    row_bound: int
    reason: str


# `schema.py`'s own "Deliberately unindexed (issue #520)" comment (near `asks`) is the one
# home for why these six stay near-empty by design — pointed at here, not restated
# (`bzh:one-prose-home`).
_SCHEMA_520_REASON = (
    "schema.py's own 'Deliberately unindexed (issue #520)' comment beside `asks`: "
    "near-empty by design, so a scan beats an index's upkeep."
)

# Every table below is append-only across ONE runner's own local store, never the whole
# fleet's: `winter ws init` provisions a fresh environment (and so a fresh runner store)
# per feature, so even a table's full history — not just its currently-live rows — stays
# small for the environment's development lifetime.
_ENV_SCOPED_HISTORY_REASON = (
    "one feature environment's own local runner store (a fresh one per `winter ws init`, "
    "never shared fleet-wide) — even this table's full history across that one "
    "environment's development lifetime stays well under the threshold."
)

# Every table below records a rare, human- or operator-triggered event (a pause, a
# takeover, a requeue, a hub-resolved escalation, a restart) — sparse by the nature of
# the event itself, independent of how long the environment has been running.
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
