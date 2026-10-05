"""Proposed-work-item authorization — reject submitted proposals unless the node
declares ``proposes_work_items``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.proposals import ItemProposal
from blizzard.hub.domain.graph.model import Node


@domain_model
@dataclass(frozen=True)
class ProposalPolicy:
    """A node's ``proposes_work_items`` policy judged against one submission's proposals —
    already-loaded values only (``bzh:domain-takes-objects``)."""

    node: Node
    proposals: Sequence[ItemProposal]

    def rejection(self) -> str | None:
        """A failure detail naming the node, or ``None`` when no proposal is refused."""
        if not self.proposals or self.node.proposes_work_items:
            return None
        return f"node `{self.node.name}` does not declare `proposes_work_items` but its completion carries proposals"
