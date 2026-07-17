"""On-demand external-merge detection for open-pr deliveries.

The open-pr deliver mode parks a chunk on an opened PR without a terminal
transition — the chunk derives ``delivering`` (awaiting an external merge) with its
environments held. This service is the *detection* half: it polls each open PR
through the forge seam (``check_pr``) and, once **every** PR has reached a terminal
state, terminates the delivery through :meth:`finalize_pr_delivery` — the ``pr.closed``
facts, the hub lease, the terminal transition, and the route release, written atomically
so the chunk flips to ``done`` and the runner releases its environments.

It backs the on-demand ``POST /chunks/{id}/check-delivery`` route (the impatient path);
a background poll tick would call the same method (out of MVP scope). Nothing terminal
happens while any PR is still open — the chunk stays parked, idempotent across repeated
checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import TRANSITION_PREFIX, mint
from blizzard.hub.delivery.forge import IForgeDelivery, PrDisposition, PrHandle
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Graph
from blizzard.hub.domain.work import Chunk, IWriteChunkRepository, PrClosedFact, newest_transition

_HUB_RUNNER_ID = "hub"


@dataclass(frozen=True)
class CheckDeliveryResult:
    """The outcome of one on-demand delivery check."""

    finalized: bool  # True iff this check terminated the delivery (PRs all terminal)
    open_prs: int  # PRs still awaiting an external merge after this check
    detail: str


class DeliveryCheckService:
    """Polls a parked open-pr chunk's PRs and finalizes once all are terminal."""

    def __init__(self, *, chunks: IWriteChunkRepository, forge: IForgeDelivery, clock: IClock) -> None:
        self._chunks = chunks
        self._forge = forge
        self._clock = clock

    def check(self, chunk: Chunk, graph: Graph) -> CheckDeliveryResult:
        facts = self._chunks.load_facts(chunk.chunk_id)
        if facts is None:
            return CheckDeliveryResult(finalized=False, open_prs=0, detail="chunk has no facts")
        if facts.pr_closed:
            return CheckDeliveryResult(finalized=False, open_prs=0, detail="delivery already finalized")
        handles = facts.pr_opened
        if not handles:
            return CheckDeliveryResult(finalized=False, open_prs=0, detail="chunk has no open PR to check")

        states = [(h, self._forge.check_pr(PrHandle(repo=h.repo, number=h.number, url=h.url))) for h in handles]
        still_open = [h for h, st in states if st.disposition is PrDisposition.OPEN]
        if still_open:
            return CheckDeliveryResult(
                finalized=False, open_prs=len(still_open), detail=f"{len(still_open)} PR(s) still open"
            )

        closed = [
            PrClosedFact(
                repo=h.repo,
                number=h.number,
                merged=st.disposition is PrDisposition.MERGED,
                landed_commit=st.landed_commit,
            )
            for h, st in states
        ]
        current = newest_transition(facts)
        deliver_node_id = current.to_node_id if current is not None else graph.entry_node_id
        hub_epoch = (current.epoch if current is not None else 0) + 1
        all_merged = all(pr.merged for pr in closed)
        wrote = self._chunks.finalize_pr_delivery(
            chunk.chunk_id,
            closed=closed,
            from_node_id=deliver_node_id,
            to_node_id=RESERVED_TERMINAL,
            choice_name="landed" if all_merged else "closed",
            epoch=hub_epoch,
            runner_id=_HUB_RUNNER_ID,
            transition_id=mint(TRANSITION_PREFIX, self._clock),
            at=self._clock.now(),
        )
        disposition = "merged" if all_merged else "closed without merge"
        return CheckDeliveryResult(finalized=wrote, open_prs=0, detail=f"delivery finalized ({disposition})")
