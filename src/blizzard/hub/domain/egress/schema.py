"""The ``steps`` and ``invocations`` dataset schemas, and the encoding of a typed row into an :class:`EgressRow`.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/rows.md`` §Datasets. Pure. A schema's column names are the
row dataclass's field names, in order, so a row cannot grow a value its schema does not name."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, date, datetime

from blizzard.hub.domain.egress.repository import UsagePosition
from blizzard.hub.domain.egress.rows import InvocationRow, StepRow
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.egress.writer import ColumnType, DatasetColumn, DatasetSchema, EgressRow, rfc3339_utc

__all__ = [
    "INVOCATIONS_SCHEMA",
    "STEPS_SCHEMA",
    "invocation_egress_row",
    "partition_of",
    "step_egress_row",
    "step_position",
    "usage_position_text",
]

_S, _I, _B, _T, _M, _L = (
    ColumnType.STRING,
    ColumnType.INT64,
    ColumnType.BOOL,
    ColumnType.TIMESTAMP,
    ColumnType.MONEY,
    ColumnType.STRING_LIST,
)

# name, type, nullable, meaning — rows.md's table, one entry per column in row-field order.
_STEPS: tuple[tuple[str, ColumnType, bool, str], ...] = (
    ("step_key", _S, False, "The row's identity: chunk_id/epoch, or chunk_id/epoch/gate/decision_id for a gate step"),
    ("trace_id", _S, False, "The step's derived trace id, as 32 hex characters, so a row joins to its trace"),
    ("step_kind", _S, False, "runner, hub or gate"),
    ("chunk_id", _S, False, "The chunk"),
    ("work_refs", _L, False, "Its work items as source-native tokens; empty when it has none"),
    ("sources", _L, False, "The distinct work sources of those items"),
    ("graph_id", _S, False, "The graph the step stood in"),
    ("graph_name", _S, False, "The graph's name"),
    ("node_id", _S, False, "The node"),
    ("node_name", _S, False, "The node's name"),
    ("epoch", _I, False, "The epoch"),
    ("decision_id", _S, True, "A gate step's decision; null otherwise"),
    ("visit", _I, False, "Which arrival at this node this is, counting from 1"),
    ("runner_id", _S, True, "The runner that held a runner step; null for hub and gate steps"),
    ("harness_id", _S, True, "The harness of the step's last invocation"),
    ("models", _L, False, "Distinct models across its invocations"),
    ("started_at", _T, False, "When the step started"),
    ("ended_at", _T, False, "When the step ended; a gate ends at resolved_at when resolved"),
    ("closed_at", _T, False, "When the closing fact was recorded; equal to ended_at except for a resolved gate"),
    ("duration_ms", _I, False, "ended_at minus started_at"),
    ("outcome", _S, False, "How the step ended"),
    ("choice", _S, True, "The resolved choice, when there is one"),
    ("to_node_name", _S, True, "Where it led: a node name, done, or graph:<name>"),
    ("preceded_by", _S, True, "restart, requeue or released-claim"),
    ("bounce_cause", _S, True, "conflict, checks, master-moved, poll-timeout, or a choice"),
    ("asks", _I, False, "Asks raised in the step"),
    ("asks_unanswered", _I, False, "How many asks were never answered"),
    ("wait_queue_ms", _I, False, "Queue wait, summed"),
    ("wait_claim_ms", _I, False, "Claim wait, summed"),
    ("wait_ask_ms", _I, False, "Ask wait, summed"),
    ("wait_pause_ms", _I, False, "Pause wait, summed"),
    ("wait_pickup_ms", _I, False, "Decision pickup wait, summed"),
    ("invocations", _I, False, "How many invocations rows belong to the step"),
    ("input_tokens", _I, False, "Summed across its invocations, with blizzard's uncached input count"),
    ("output_tokens", _I, False, "Summed across its invocations"),
    ("cache_read_tokens", _I, False, "Summed across its invocations"),
    ("cache_create_tokens", _I, False, "Summed across its invocations"),
    ("cost_billed_usd", _M, True, "The harness-billed cost, summed; null when no invocation carried one"),
    ("cost_estimated_usd", _M, True, "The estimated cost, summed; null when no invocation carried one"),
    ("cost_partial", _B, False, "Some invocation carried neither a billed nor an estimated cost"),
    ("billed_partial", _B, False, "Some invocation carried no billed cost"),
    ("exported_at", _T, False, "When this copy of the row was written"),
)

_INVOCATIONS: tuple[tuple[str, ColumnType, bool, str], ...] = (
    ("usage_id", _I, False, "The row's identity: the usage_facts id"),
    ("step_key", _S, False, "The runner step it belongs to, by chunk and epoch"),
    ("trace_id", _S, False, "The step's derived trace id, as 32 hex characters"),
    ("chunk_id", _S, False, "The chunk"),
    ("epoch", _I, False, "The epoch"),
    ("graph_id", _S, False, "The graph its step stood in"),
    ("graph_name", _S, False, "The graph's name"),
    ("node_id", _S, False, "The node"),
    ("node_name", _S, False, "The node's name"),
    ("runner_id", _S, False, "The runner that reported it"),
    ("kind", _S, False, "spawn, resume or judge; a nudge reads resume"),
    ("model", _S, False, "As reported"),
    ("harness_id", _S, True, "As reported"),
    ("harness_version", _S, True, "As reported"),
    ("input_tokens", _I, False, "As reported"),
    ("output_tokens", _I, False, "As reported"),
    ("cache_read_tokens", _I, False, "As reported"),
    ("cache_create_tokens", _I, False, "As reported"),
    ("cost_billed_usd", _M, True, "As reported; null when absent"),
    ("cost_estimated_usd", _M, True, "As reported; null when absent"),
    ("recorded_at", _T, False, "When the hub received it"),
    ("exported_at", _T, False, "When this copy of the row was written"),
)


def _schema(name: str, columns: tuple[tuple[str, ColumnType, bool, str], ...]) -> DatasetSchema:
    return DatasetSchema(name, 1, tuple(DatasetColumn(*column) for column in columns))


STEPS_SCHEMA = _schema("steps", _STEPS)
INVOCATIONS_SCHEMA = _schema("invocations", _INVOCATIONS)


def step_position(key: CursorKey) -> str:
    """An opaque text that sorts as the step cursor's order does."""
    return f"{rfc3339_utc(key.at)}|{key.chunk_id}|{key.epoch:010d}|{key.decision_id}"


def usage_position_text(position: UsagePosition) -> str:
    """An opaque text that sorts as the usage cursor's order does."""
    return f"{rfc3339_utc(position.recorded_at)}|{position.usage_id:012d}"


def _values(row: StepRow | InvocationRow) -> dict[str, object]:
    return {f.name: getattr(row, f.name) for f in fields(row)}


def step_egress_row(row: StepRow, key: CursorKey) -> EgressRow:
    return EgressRow(step_position(key), _values(row))


def invocation_egress_row(row: InvocationRow) -> EgressRow:
    return EgressRow(usage_position_text(UsagePosition(row.recorded_at, row.usage_id)), _values(row))


def partition_of(at: datetime) -> date:
    """The UTC date partition of a row's own time."""
    return at.astimezone(UTC).date()
