"""The ``steps`` and ``invocations`` egress rows, as typed records.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/rows.md`` §Datasets and §Shared conventions. Pure: a
:class:`StepSummary` (or a usage row and its chunk's :class:`StepFacts`) in, one row out. A row only renames what the
summary already computed, so it cannot disagree with the step's trace. Values are typed, not formatted: turning times,
money and lists into a file format is the writer's job."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from blizzard.foundation.roles import domain_model
from blizzard.foundation.trace_ids import StepKey, trace_id
from blizzard.hub.domain.chunk.model import UsageFact
from blizzard.hub.domain.observability.tracing.facts import StepFacts
from blizzard.hub.domain.observability.tracing.steps import NodeStep, StepKind
from blizzard.hub.domain.observability.tracing.summary import StepSummary

_MONEY_SCALE = Decimal("0.000000001")


@domain_model
@dataclass(frozen=True)
class ExportedStep:
    """One ``steps`` row; field order is the column order."""

    step_key: str
    trace_id: str
    step_kind: str
    chunk_id: str
    work_refs: tuple[str, ...]
    sources: tuple[str, ...]
    graph_id: str
    graph_name: str
    node_id: str
    node_name: str
    epoch: int
    decision_id: str | None
    visit: int
    runner_id: str | None
    runner_name: str | None
    harness_id: str | None
    models: tuple[str, ...]
    started_at: datetime
    ended_at: datetime
    closed_at: datetime
    duration_ms: int
    outcome: str
    choice: str | None
    to_node_name: str | None
    preceded_by: str | None
    bounce_cause: str | None
    asks: int
    asks_unanswered: int
    wait_queue_ms: int
    wait_claim_ms: int
    wait_ask_ms: int
    wait_pause_ms: int
    wait_pickup_ms: int
    invocations: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_billed_usd: Decimal | None
    cost_estimated_usd: Decimal | None
    cost_partial: bool
    billed_partial: bool
    exported_at: datetime


@domain_model
@dataclass(frozen=True)
class AttributedUsage:
    """A hub ``usage_facts`` row with the identity columns :class:`UsageFact` does not carry."""

    usage_id: int
    chunk_id: str
    runner_id: str
    fact: UsageFact


@domain_model
@dataclass(frozen=True)
class ExportedInvocation:
    """One ``invocations`` row; field order is the column order."""

    usage_id: int
    step_key: str
    trace_id: str
    chunk_id: str
    epoch: int
    graph_id: str
    graph_name: str
    node_id: str
    node_name: str
    runner_id: str
    runner_name: str | None
    kind: str
    model: str
    harness_id: str | None
    harness_version: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_billed_usd: Decimal | None
    cost_estimated_usd: Decimal | None
    recorded_at: datetime
    exported_at: datetime


def money(amount: float | None) -> Decimal | None:
    """A cost at the contract's scale, through its shortest decimal form so no float noise rides along."""
    return None if amount is None else Decimal(repr(amount)).quantize(_MONEY_SCALE)


def trace_id_text(key: StepKey) -> str:
    return f"{trace_id(key):032x}"


def step_row(summary: StepSummary, exported_at: datetime) -> ExportedStep:
    """The ``steps`` row of a closed step. ``runner_id`` and ``runner_name`` name the holder of a runner step only."""
    return ExportedStep(
        step_key=summary.step_key.text(),
        trace_id=trace_id_text(summary.step_key),
        step_kind=summary.kind.value,
        chunk_id=summary.chunk_id,
        work_refs=summary.work_refs,
        sources=summary.work_sources,
        graph_id=summary.graph_id,
        graph_name=summary.graph_name,
        node_id=summary.node_id,
        node_name=summary.node_name,
        epoch=summary.epoch,
        decision_id=summary.decision_id,
        visit=summary.visit,
        runner_id=summary.runner_id if summary.kind is StepKind.RUNNER else None,
        runner_name=summary.runner_name if summary.kind is StepKind.RUNNER else None,
        harness_id=summary.harness_id,
        models=summary.models,
        started_at=summary.started_at,
        ended_at=summary.ended_at,
        closed_at=summary.closed_at,
        duration_ms=round((summary.ended_at - summary.started_at).total_seconds() * 1000),
        outcome=summary.outcome.value,
        choice=summary.choice,
        to_node_name=summary.to_node_name,
        preceded_by=summary.preceded_by.value if summary.preceded_by is not None else None,
        bounce_cause=summary.bounce_cause,
        asks=summary.asks,
        asks_unanswered=summary.asks_unanswered,
        wait_queue_ms=summary.wait_queue_ms,
        wait_claim_ms=summary.wait_claim_ms,
        wait_ask_ms=summary.wait_ask_ms,
        wait_pause_ms=summary.wait_pause_ms,
        wait_pickup_ms=summary.wait_pickup_ms,
        invocations=summary.invocations,
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
        cache_read_tokens=summary.cache_read_tokens,
        cache_create_tokens=summary.cache_create_tokens,
        cost_billed_usd=money(summary.cost_billed_usd),
        cost_estimated_usd=money(summary.cost_estimated_usd),
        cost_partial=summary.cost_partial,
        billed_partial=summary.billed_partial,
        exported_at=exported_at,
    )


def invocation_row(
    facts: StepFacts, step: NodeStep, usage: AttributedUsage, exported_at: datetime
) -> ExportedInvocation:
    """The ``invocations`` row of one usage row, positioned by ``step`` — the runner step holding its epoch,
    open or closed; its runner's name is ``facts``' owner of the usage's epoch."""
    if usage.chunk_id != facts.chunk_id:
        raise ValueError(f"usage {usage.usage_id} belongs to {usage.chunk_id}, not {facts.chunk_id}")
    fact = usage.fact
    return ExportedInvocation(
        usage_id=usage.usage_id,
        step_key=step.key.text(),
        trace_id=trace_id_text(step.key),
        chunk_id=usage.chunk_id,
        epoch=fact.epoch,
        graph_id=step.position.graph_id,
        graph_name=facts.graphs[step.position.graph_id].name,
        node_id=step.position.node_id,
        node_name=step.position.node_name,
        runner_id=usage.runner_id,
        runner_name=facts.runner_names.get(usage.runner_id),
        kind=fact.kind,
        model=fact.model,
        harness_id=fact.harness_id,
        harness_version=fact.harness_version,
        input_tokens=fact.input_tokens,
        output_tokens=fact.output_tokens,
        cache_read_tokens=fact.cache_read_tokens,
        cache_create_tokens=fact.cache_create_tokens,
        cost_billed_usd=money(fact.cost_usd),
        cost_estimated_usd=money(fact.estimated_cost_usd),
        recorded_at=fact.recorded_at,
        exported_at=exported_at,
    )
