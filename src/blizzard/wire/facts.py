"""Runner→hub fact intake bodies.

Two shapes land the same fleet-visible runner-minted facts, and they coexist:

* The **batched store-and-forward push** — :class:`RunnerFactBatch` to ``POST
  /api/events`` — is the canonical intake: every hub-bound fact
  rides the runner's outbound buffer stamped with a **per-runner monotonic sequence
  number**, and the hub applies it idempotently against a per-runner **high-water
  mark** (a seq ≤ mark is already-applied and re-acked without re-applying — the
  replay after a lost ack or an outage backlog drain). The reconciliation loop
  reports every ``lease.minted`` / ``escalation.recorded`` this way.
* The **per-fact typed bodies** — :class:`LeaseMintReport` / :class:`EscalationReport`
  to ``POST /chunks/{id}/leases`` and ``/chunks/{id}/escalations`` — are the direct,
  non-buffered intake a caller uses to land a single fact (the domain rule is
  :class:`~blizzard.hub.domain.facts.RunnerFactsService`).

Completions do **not** ride either fact route: they carry the chunk's next-node
envelope in their reply, so they keep their own ``POST /chunks/{id}/completions`` route
(already epoch-idempotent).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Fact kinds the batched /events push accepts (``noun.verb`` names).
LEASE_MINTED = "lease.minted"
ESCALATION_RECORDED = "escalation.recorded"
# The ask/answer pair the runner forwards up: question.asked lands
# the durable question row (the chunk derives waiting_on_human), answer.delivered
# records that the resume-with-answer ran (board detail, status already flipped).
QUESTION_ASKED = "question.asked"
ANSWER_DELIVERED = "answer.delivered"
# The runner's *own* brake, reported upward so the board can render it (issue #43). Named
# apart from the hub's `runner.paused` / `runner.resumed` on purpose: they are two
# concepts, not two spellings. The hub's is the fleet coercing a runner; this is the runner
# declining to claim, set machine-locally and true even when the hub never heard about it.
# Runner-scoped: no chunk_id, no lease_id.
RUNNER_LOCALLY_PAUSED = "runner.locally_paused"
RUNNER_LOCALLY_RESUMED = "runner.locally_resumed"
# One harness invocation's usage/cost telemetry (epic #57, issue #58) — a fact, never a
# stored aggregate: the hub derives a chunk's total by summing these at read time
# (Phase 3 of the epic). Payload: {chunk_id, node_id, epoch, kind, model, input_tokens,
# output_tokens, cache_read_tokens, cache_create_tokens, cost_usd|null}. Idempotency
# rides the same per-runner outbound seq every other fact here does — the runner-local
# `(lease_id, generation, kind)` key is a *local* write-once guard
# (`IWriteRunnerStore.record_usage`), not part of this wire shape.
USAGE_RECORDED = "usage.recorded"
# One operationally-significant failure the runner surfaces for the hub's event log
# (issue #125) — a worker non-clean exit, a captured spawn/push/attach command failure, a
# stall. Folded into the append-only `event_log` and re-broadcast on the SSE spine as
# `event-logged`. Payload (open, like `usage.recorded`): {severity (info|warning|critical),
# kind, chunk_id|null (a runner-scoped event names none), lease_id|null, node_name|null,
# message, detail (object|null)}. Idempotency rides the same per-runner outbound seq every
# other fact here does; deliberately NOT route-token-gated — a failure event from a
# fenced-out or dying worker is exactly what an operator must see.
EVENT_RECORDED = "event.recorded"


class LeaseMintReport(BaseModel):
    """A runner's ``lease.minted`` — one node-step attempt's fencing epoch."""

    epoch: int
    runner_id: str


class EscalationReport(BaseModel):
    """A runner's ``escalation.recorded`` — retries exhausted for the node.

    ``takeover_command`` is the literal ``cd <workdir> && <harness resume>`` a human
    pastes to enter the parked session; ``epoch`` is the
    exhausted attempt's fence, closed by a later lease mint."""

    epoch: int
    runner_id: str
    takeover_command: str = ""


class RunnerFact(BaseModel):
    """One buffered runner fact: its per-runner seq, its kind, and its payload.

    ``payload`` is the kind-specific body — for ``lease.minted`` ``{chunk_id, epoch,
    route_token}``, for ``escalation.recorded`` ``{chunk_id, epoch, takeover_command,
    route_token}``, for ``question.asked`` the ask fields plus ``route_token`` — kept
    open so a new runner fact kind bolts on without a wire change. ``route_token``
    (issue #84a) is the chunk-scoped fact's route capability token, stamped at
    enqueue; present-only in this phase (the hub does not yet reject on it).
    """

    seq: int
    kind: str
    payload: dict[str, Any] = {}


class RunnerFactBatch(BaseModel):
    """A runner's push of one-or-more buffered facts, ordered by seq."""

    runner_id: str
    facts: list[RunnerFact]


class RunnerFactAck(BaseModel):
    """The hub's per-batch acknowledgement against its high-water mark.

    ``high_water`` is the runner's new mark after this batch; ``applied`` and
    ``already_applied`` partition the pushed seqs so the runner can ack its buffer
    (a semantic rejection still acks — rejection is an outcome, not a delivery
    failure). ``rejected`` names seqs the hub refused for a non-idempotency
    reason (an unknown kind), which the runner surfaces rather than silently drops.
    """

    runner_id: str
    high_water: int
    applied: list[int] = []
    already_applied: list[int] = []
    rejected: list[int] = []
