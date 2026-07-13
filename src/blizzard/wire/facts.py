"""Runner→hub fact intake bodies (D-044/D-069).

Two shapes land the same fleet-visible runner-minted facts, and they coexist:

* The **batched store-and-forward push** — :class:`RunnerFactBatch` to ``POST
  /api/events`` — is the canonical intake (domain/events.md): every hub-bound fact
  rides the runner's outbound buffer stamped with a **per-runner monotonic sequence
  number**, and the hub applies it idempotently against a per-runner **high-water
  mark** (a seq ≤ mark is already-applied and re-acked without re-applying — the
  replay after a lost ack or an outage backlog drain, D-069). The reconciliation loop
  reports every ``lease.minted`` / ``escalation.recorded`` this way.
* The **per-fact typed bodies** — :class:`LeaseMintReport` / :class:`EscalationReport`
  to ``POST /chunks/{id}/leases`` and ``/chunks/{id}/escalations`` — are the direct,
  non-buffered intake a caller uses to land a single fact (the domain rule is
  :class:`~blizzard.hub.domain.facts.RunnerFactsService`).

Completions do **not** ride either fact route: they carry the chunk's next-node
envelope in their reply, so they keep their own ``POST /chunks/{id}/completions`` route
(already epoch-idempotent, D-090).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Fact kinds the batched /events push accepts (domain/events.md ``noun.verb`` names).
LEASE_MINTED = "lease.minted"
ESCALATION_RECORDED = "escalation.recorded"


class LeaseMintReport(BaseModel):
    """A runner's ``lease.minted`` — one node-step attempt's fencing epoch (D-044)."""

    epoch: int
    runner_id: str


class EscalationReport(BaseModel):
    """A runner's ``escalation.recorded`` — retries exhausted for the node (D-009).

    ``takeover_command`` is the literal ``cd <workdir> && <harness resume>`` a human
    pastes to enter the parked session (design/harness-adapters.md); ``epoch`` is the
    exhausted attempt's fence, closed by a later lease mint (D-067)."""

    epoch: int
    runner_id: str
    takeover_command: str = ""


class RunnerFact(BaseModel):
    """One buffered runner fact: its per-runner seq, its kind, and its payload.

    ``payload`` is the kind-specific body — for ``lease.minted`` ``{chunk_id, epoch}``,
    for ``escalation.recorded`` ``{chunk_id, epoch, takeover_command}`` — kept open so a
    new runner fact kind bolts on without a wire change.
    """

    seq: int
    kind: str
    payload: dict[str, Any] = {}


class RunnerFactBatch(BaseModel):
    """A runner's push of one-or-more buffered facts, ordered by seq (D-069)."""

    runner_id: str
    facts: list[RunnerFact]


class RunnerFactAck(BaseModel):
    """The hub's per-batch acknowledgement against its high-water mark.

    ``high_water`` is the runner's new mark after this batch; ``applied`` and
    ``already_applied`` partition the pushed seqs so the runner can ack its buffer
    (a semantic rejection still acks — rejection is an outcome, not a delivery
    failure, D-069). ``rejected`` names seqs the hub refused for a non-idempotency
    reason (an unknown kind), which the runner surfaces rather than silently drops.
    """

    runner_id: str
    high_water: int
    applied: list[int] = []
    already_applied: list[int] = []
    rejected: list[int] = []
