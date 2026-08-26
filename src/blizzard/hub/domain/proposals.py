"""Proposed-work-item domain — a node-step's completion carrying proposed work items
alongside its artifacts (D1, D2). Read by the delivery-materialization sweep
(``blizzard.hub.domain.work_item_materialization``, blizzard#366)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blizzard.wire.completion import WorkItemProposal


@dataclass(frozen=True)
class WorkItemProposalRow:
    """One proposed work item's flat storage row — riding a node-step's completion
    (``create`` or ``update``, D1). ``data`` is the kind-shaped payload as JSON: ``create``
    carries ``{title, body, stated_priority}``, ``update`` carries ``{source, ref,
    evidence}``. ``ordinal`` is the authored-submission position (``graph_artifacts``-shaped).
    ``runner_id`` is the proposing runner (D4) — ``None`` only for a row written before
    that column existed."""

    proposal_id: str
    chunk_id: str
    node_id: str
    node_name: str
    epoch: int
    ordinal: int
    kind: str
    data: str
    runner_id: str | None

    @classmethod
    def of(
        cls,
        proposal: WorkItemProposal,
        *,
        proposal_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        ordinal: int,
        runner_id: str,
    ) -> WorkItemProposalRow:
        """Compress a wire proposal to its storage row. ``model_dump_json`` derives
        ``data`` from whichever variant this is, so a field added to either
        ``CreateWorkItemProposal`` or ``UpdateWorkItemProposal`` lands here automatically."""
        return cls(
            proposal_id=proposal_id,
            chunk_id=chunk_id,
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            ordinal=ordinal,
            kind=proposal.kind,
            data=proposal.model_dump_json(exclude={"kind"}),
            runner_id=runner_id,
        )
