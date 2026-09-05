"""Runner→hub fact intake bodies.

Two coexisting intakes for the same runner-minted facts: a batched store-and-forward push,
where each fact carries a **per-runner monotonic seq** applied idempotently against a
per-runner **high-water mark**, and a direct per-fact body for landing a single fact.
Completions ride neither, since they carry the next-node envelope in their reply."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Fact kinds the batched /events push accepts (``noun.verb`` names).
LEASE_MINTED = "lease.minted"
ESCALATION_RECORDED = "escalation.recorded"
# question.asked opens the ask; answer.delivered records that the resume ran.
QUESTION_ASKED = "question.asked"
ANSWER_DELIVERED = "answer.delivered"
# The runner's *own* brake, reported upward (issue #43) — a distinct concept from the
# hub's own pause, not a second spelling of it. Runner-scoped: no chunk_id, no lease_id.
RUNNER_LOCALLY_PAUSED = "runner.locally_paused"
RUNNER_LOCALLY_RESUMED = "runner.locally_resumed"
# One harness invocation's usage/cost telemetry (issue #58) — a fact, never a stored
# aggregate. Payload: {chunk_id, node_id, epoch, kind, model, tokens…, cost_usd|null}.
USAGE_RECORDED = "usage.recorded"
# One operationally-significant failure (issue #125). Payload: {severity, kind,
# chunk_id|null, lease_id|null, node_name|null, message, detail|null}. Never token-gated.
EVENT_RECORDED = "event.recorded"
# An advisory sample of subscription rate-limit utilization (issue #218), never one a
# status derives from. Payload: {sampled_at, windows: [...], slug|null, name|null};
# upserted per (runner_id, slug), not appended — a fact missing slug/name lands under
# the legacy slug and its own name.
EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED = "external_subscription_usage.sampled"

# The join key a runner with no `[[subscription]]` declarations gets exactly one
# declaration synthesized under. Declared once here, in the one module both daemons
# already depend on, and imported everywhere else — a frozen migration restates it
# instead, because a migration may import no live application code (`bzh:frozen-revisions`).
LEGACY_ANTHROPIC_SLUG = "anthropic"
# The legacy slug's operator-facing label — the synthesized declaration's own `name` and
# the migration backfills' `name`, so an unupgraded runner's display label never flips case.
LEGACY_ANTHROPIC_NAME = "Anthropic"

# The Anthropic provider-sampler binding's own selector value (blizzard#436) — distinct from
# `LEGACY_ANTHROPIC_SLUG`, which identifies a *declaration*, not a provider; the two happen
# to share a literal today, but a config change to one must not silently unbind the other.
PROVIDER_ANTHROPIC = "anthropic"


class LeaseMintReport(BaseModel):
    """A runner's ``lease.minted`` — one node-step attempt's fencing epoch."""

    epoch: int
    runner_id: str


class EscalationReport(BaseModel):
    """A runner's ``escalation.recorded`` — the runner ran out of moves on this node.
    ``takeover_command`` may carry operator prose instead of a literal command, or be empty;
    ``wrapped_takeover_command`` is the wrapped equivalent of ``takeover_command``."""

    epoch: int
    runner_id: str
    takeover_command: str = ""
    wrapped_takeover_command: str = ""


class RunnerFact(BaseModel):
    """One buffered runner fact: its per-runner seq, its kind, and its payload.

    ``payload`` is the kind-specific body, kept open so a new fact kind needs no wire change;
    every chunk-scoped kind carries ``route_token`` (issue #84a), stamped at enqueue."""

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
