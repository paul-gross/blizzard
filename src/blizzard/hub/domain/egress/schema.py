"""The ``steps``, ``invocations`` and ``events`` dataset schemas, and the encoding of a typed row into an
:class:`EgressRow`.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/rows.md`` §Datasets and
``blizzard-product:/plans/fact-egress/events/spec/rows.md`` §Dataset: ``events``. Pure. A schema's column names are the
row dataclass's field names, in order, so a row cannot grow a value its schema does not name."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from blizzard.hub.domain.egress.repository import EventsPosition, UsagePosition
from blizzard.hub.domain.egress.rows import InvocationRow, StepRow
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.egress.writer import ColumnType, DatasetColumn, DatasetSchema, EgressRow, rfc3339_utc

if TYPE_CHECKING:
    from blizzard.hub.domain.egress.event_rows import EventsRow

__all__ = [
    "EVENTS_SCHEMA",
    "INVOCATIONS_SCHEMA",
    "STEPS_SCHEMA",
    "events_egress_row",
    "events_position_text",
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

_EVENTS: tuple[tuple[str, ColumnType, bool, str], ...] = (
    ("record_type", _S, False, "derivation, event or dropped"),
    ("segment_id", _S, False, "The transcript segment"),
    ("extractor_version", _S, True, "The extractor that derived it; null on a dropped row"),
    ("derivation_id", _S, True, "The derivation's identity, as 32 hex characters; null on a dropped row"),
    ("derived_at", _T, True, "When the hub derived it; null on a dropped row"),
    ("complete", _B, True, "The marker's own flag: false when the derivation stopped short; derivation rows only"),
    ("event_count", _I, True, "How many event rows the derivation holds; derivation rows only"),
    ("dropped_at", _T, True, "When the hub dropped the segment's events; dropped rows only"),
    ("kind", _S, True, "file_read, skill_invocation or agent_spawn; extensible; event rows only"),
    ("subject", _S, True, "The path, the skill name, or the spawned agent type; null when the extractor names none"),
    ("tool", _S, True, "The tool the turn called"),
    ("turn_path", _S, True, "The event's place in the segment; event rows only"),
    ("occurrence", _I, True, "The event's place in the segment; event rows only"),
    ("occurred_at", _T, True, "The turn's own time, when the transcript carries one"),
    ("depth", _I, True, "0 for the main conversation, plus one per subagent nesting; event rows only"),
    ("agent_type", _S, True, "The nearest enclosing subagent's type; null at depth 0"),
    ("step_key", _S, False, "The runner step the segment came from, by chunk and epoch"),
    ("trace_id", _S, False, "That step's derived trace id, as 32 hex characters"),
    ("step_started_at", _T, False, "When that step started; the row's partition and backfill time"),
    ("chunk_id", _S, False, "The segment's chunk"),
    ("epoch", _I, False, "The segment's epoch"),
    ("spawn_generation", _I, False, "Which worker spawn on the lease produced the segment"),
    ("graph_id", _S, False, "The graph the step stood in"),
    ("graph_name", _S, False, "The graph's name"),
    ("node_id", _S, False, "The node"),
    ("node_name", _S, False, "The node's name"),
    ("harness_id", _S, True, "As derived; event rows only"),
    ("harness_version", _S, True, "As derived; event rows only"),
    ("model", _S, True, "As derived; event rows only"),
    ("effort", _S, True, "As derived; event rows only"),
    ("exported_at", _T, False, "When this copy of the row was written"),
)


def _schema(name: str, columns: tuple[tuple[str, ColumnType, bool, str], ...]) -> DatasetSchema:
    return DatasetSchema(name, 1, tuple(DatasetColumn(*column) for column in columns))


STEPS_SCHEMA = _schema("steps", _STEPS)
INVOCATIONS_SCHEMA = _schema("invocations", _INVOCATIONS)
EVENTS_SCHEMA = _schema("events", _EVENTS)


def step_position(key: CursorKey) -> str:
    """An opaque text that sorts as the step cursor's order does."""
    return f"{rfc3339_utc(key.at)}|{key.chunk_id}|{key.epoch:010d}|{key.decision_id}"


def usage_position_text(position: UsagePosition) -> str:
    """An opaque text that sorts as the usage cursor's order does."""
    return f"{rfc3339_utc(position.recorded_at)}|{position.usage_id:012d}"


def events_position_text(position: EventsPosition) -> str:
    """An opaque text that sorts as the events cursor's order does."""
    return f"{rfc3339_utc(position.at)}|{position.segment_id}|{position.extractor_version}"


def _values(row: StepRow | InvocationRow | EventsRow) -> dict[str, object]:
    return {f.name: getattr(row, f.name) for f in fields(row)}


def step_egress_row(row: StepRow, key: CursorKey) -> EgressRow:
    return EgressRow(step_position(key), _values(row))


def invocation_egress_row(row: InvocationRow) -> EgressRow:
    return EgressRow(usage_position_text(UsagePosition(row.recorded_at, row.usage_id)), _values(row))


def events_egress_row(row: EventsRow, position: EventsPosition) -> EgressRow:
    return EgressRow(events_position_text(position), _values(row))


def partition_of(at: datetime) -> date:
    """The UTC date partition of a row's own time."""
    return at.astimezone(UTC).date()
