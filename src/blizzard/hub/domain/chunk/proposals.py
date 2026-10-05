"""Proposed-work-item domain — a node-step's completion carrying proposed work items
alongside its artifacts. Read by the delivery-materialization sweep
(``blizzard.hub.domain.work_items.materialization``)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import ClassVar, Literal

from blizzard.foundation.roles import domain_model
from blizzard.foundation.work_items import WorkItemPriority


@domain_model
@dataclass(frozen=True)
class CreateItemProposal:
    """A proposed new work item — a title, a markdown body, and a stated priority."""

    kind: ClassVar[Literal["create"]] = "create"

    title: str
    body: str
    stated_priority: WorkItemPriority = WorkItemPriority.NORMAL


@domain_model
@dataclass(frozen=True)
class UpdateItemProposal:
    """A proposed update to an existing work item — its ``{source, ref}`` pointer plus evidence
    to append. An unresolvable pointer is recorded, not refused: materialization resolves it."""

    kind: ClassVar[Literal["update"]] = "update"

    source: str
    ref: str
    evidence: str


type ItemProposal = CreateItemProposal | UpdateItemProposal


@domain_model
@dataclass(frozen=True)
class StampedWorkItemProposal:
    """One proposed work item's flat storage row — riding a node-step's completion
    (``create`` or ``update``). ``data`` is the kind-shaped payload as JSON: ``create``
    carries ``{title, body, stated_priority}``, ``update`` carries ``{source, ref,
    evidence}``. ``ordinal`` is the authored-submission position (``graph_artifacts``-shaped).
    ``runner_id`` is the proposing runner — ``None`` only for a row written before
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
        proposal: ItemProposal,
        *,
        proposal_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        ordinal: int,
        runner_id: str,
    ) -> StampedWorkItemProposal:
        """Compress a proposal to its storage row. ``data`` serializes whichever variant this is
        field by field, compact and unescaped, so a field added to either lands here too."""
        return cls(
            proposal_id=proposal_id,
            chunk_id=chunk_id,
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            ordinal=ordinal,
            kind=proposal.kind,
            data=json.dumps(asdict(proposal), separators=(",", ":"), ensure_ascii=False),
            runner_id=runner_id,
        )
