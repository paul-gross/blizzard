"""Runner→hub fact intake bodies.

Two coexisting intakes for the same runner-minted facts: a batched store-and-forward push,
where each fact carries a **per-runner monotonic seq** applied idempotently against a
per-runner **high-water mark**, and a direct per-fact body for landing a single fact.
Completions ride neither, since they carry the next-node envelope in their reply."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# Fact kinds the batched /events push accepts (``noun.verb`` names).
LEASE_MINTED = "lease.minted"
ESCALATION_RECORDED = "escalation.recorded"
# question.asked opens the ask; answer.delivered records that the resume ran.
QUESTION_ASKED = "question.asked"
ANSWER_DELIVERED = "answer.delivered"
# The runner's *own* brake, reported upward — a distinct concept from the
# hub's own pause, not a second spelling of it. Runner-scoped: no chunk_id, no lease_id.
RUNNER_LOCALLY_PAUSED = "runner.locally_paused"
RUNNER_LOCALLY_RESUMED = "runner.locally_resumed"
# One harness invocation's usage/cost telemetry — a fact, never a stored
# aggregate. Payload: {chunk_id, node_id, epoch, kind, model, tokens…, cost_usd|null,
# estimated_cost_usd|null}, the estimate absent from a runner predating it and kept apart
# from cost_usd — a runner-side estimate for a subscription invocation, never billed spend.
USAGE_RECORDED = "usage.recorded"
# One operationally-significant failure. Payload: {severity, kind,
# chunk_id|null, lease_id|null, node_name|null, message, detail|null}. Never token-gated.
EVENT_RECORDED = "event.recorded"
# An advisory sample of subscription rate-limit utilization, never one a
# status derives from. Payload: {slug, sampled_at, windows: [...], name|null}; upserted
# per (runner_id, slug), not appended.
EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED = "external_subscription_usage.sampled"
# A sampler miss, upserted per (runner_id, slug) beside the sample. Payload: {slug, name, missed_at, reason} only.
EXTERNAL_SUBSCRIPTION_USAGE_MISSED = "external_subscription_usage.missed"
# The ``SampleMissReason`` value for a lapsed credential.
CREDENTIAL_LAPSED_MISS_REASON = "credential_lapsed"


class ExternalSubscriptionUsageWindowFact(BaseModel):
    """One complete subscription-usage window accepted from a runner fact.

    The numeric fields are strict: a ``bool`` is refused where a number belongs.
    ``resets_at`` stays lax and accepts an ISO-8601 string."""

    window: str = Field(strict=True)
    utilization_pct: float = Field(ge=0, le=100, allow_inf_nan=False, strict=True)
    resets_at: datetime
    window_seconds: int = Field(gt=0, strict=True)


class LeaseMintReport(BaseModel):
    """A runner's ``lease.minted`` — one node-step attempt's fencing epoch."""

    epoch: int
    runner_id: str
    # The minted lease — recorded as the epoch's owning lease when it is the first to name one.
    lease_id: str | None = None


class EscalationReport(BaseModel):
    """A runner's ``escalation.recorded`` — the runner ran out of moves on this node.
    ``takeover_command`` may carry operator prose instead of a literal command, or be empty;
    ``wrapped_takeover_command`` is the wrapped equivalent of ``takeover_command``."""

    epoch: int
    runner_id: str
    lease_id: str | None = None
    takeover_command: str = ""
    wrapped_takeover_command: str = ""
    cause: str | None = None
    detail: str | None = None


class RunnerFact(BaseModel):
    """One buffered runner fact: its per-runner seq, its kind, and its payload.

    ``payload`` is the kind-specific body, kept open so a new fact kind needs no wire change;
    every chunk-scoped kind carries ``route_token``, stamped at enqueue."""

    seq: int
    kind: str
    payload: dict[str, Any] = {}


class RunnerFactBatch(BaseModel):
    """A runner's push of one-or-more buffered facts, ordered by seq."""

    runner_id: str
    facts: list[RunnerFact]


class RunnerFactAck(BaseModel):
    """The hub's per-batch acknowledgement against its high-water mark.

    ``high_water`` is the new mark after this batch; ``applied``/``already_applied`` partition
    the pushed seqs, and ``rejected`` names seqs refused for a non-idempotency reason."""

    runner_id: str
    high_water: int
    applied: list[int] = []
    already_applied: list[int] = []
    rejected: list[int] = []
