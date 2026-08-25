"""Proposed-work-item authorization — the hub-side gate on a completion's ``proposals``
(D6). Unlike ``produces_auth``'s warn/enforce backstop, this refusal is unconditional: a
node declaring no ``proposes_work_items`` policy and submitting no proposals is
untouched, so there is nothing to migrate and no warn tier to earn."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.domain.graph import Node
from blizzard.wire.completion import WorkItemProposal


@dataclass(frozen=True)
class ProposalPolicy:
    """A node's ``proposes_work_items`` policy judged against one submission's proposals —
    already-loaded values only (``bzh:domain-takes-objects``)."""

    node: Node
    proposals: list[WorkItemProposal]

    def rejection(self) -> str | None:
        """A failure detail naming the node, or ``None`` to proceed. Unconditional: there
        is no mode to soften it under, unlike ``Produces.rejection``'s ``warn``/``enforce``."""
        if not self.proposals or self.node.proposes_work_items:
            return None
        return f"node `{self.node.name}` does not declare `proposes_work_items` but its completion carries proposals"
