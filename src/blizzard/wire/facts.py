"""Runner-reported fact intake bodies (D-044/D-069).

The runner reports two fleet-visible facts up to the hub over its outbound edge:
``lease.minted`` (every node-step attempt, so the hub's epoch fence tracks the
runner's, D-035/D-044) and ``escalation.recorded`` (retries exhausted, carrying the
pasteable takeover command, D-009/D-035). These are the ``POST`` bodies for the
``/chunks/{id}/leases`` and ``/chunks/{id}/escalations`` intake routes; the domain
rule is :class:`~blizzard.hub.domain.facts.RunnerFactsService`.
"""

from __future__ import annotations

from pydantic import BaseModel


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
