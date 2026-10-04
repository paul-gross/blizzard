"""The delivery-materialization sweep: a delivered chunk's accumulated
``work_item_proposals`` rows become real work items. Eventually convergent, never
atomic with the landing — domain layer only (``bzh:domain-core``): every
collaborator is either an injected Protocol or another domain-layer service
(:class:`~blizzard.hub.domain.work_items.editing.WorkItemEditService`,
:class:`~blizzard.hub.domain.graph.authoring.GraphMintService`), never an adapter, so
:meth:`WorkItemMaterializationReconciler.sweep` is one complete, directly-callable step
(``bzh:steppable-loop``)."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.chunk.model import IWriteWorkItemRepository, WorkItemMaterializationOutcome
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.proposals import StampedWorkItemProposal
from blizzard.hub.domain.graph.authoring import GraphMintService
from blizzard.hub.domain.graph.model import Graph, GraphDoc
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.domain.work_items.proposal_rules import (
    CreateProposal,
    Unresolvable,
    UpdateProposal,
    judge_update_target,
    parse_proposal,
)
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.work_item_materialization")


class WorkItemMaterializationReconciler:
    """Per not-yet-judged proposal of a delivered chunk: mint a ``create``
    proposal into the hub source, or append an ``update`` proposal's evidence to
    the item its pointer names — every proposal materializes, with no epoch filter.
    Unresolvable is recorded with its reason and never fails the sweep; a
    transient failure (a retired default graph, a pre-empted ref) records nothing and
    is retried next pass."""

    def __init__(
        self,
        *,
        delivery: IWriteChunkDeliveryRepository,
        items: IWriteWorkItemRepository,
        edits: WorkItemEditService,
        work_sources: IWorkSourceRegistry,
        graph_mint: GraphMintService,
        default_graph_doc: GraphDoc,
        default_graph_yaml: str,
        clock: IClock,
    ) -> None:
        self._delivery = delivery
        self._items = items
        self._edits = edits
        self._work_sources = work_sources
        self._graph_mint = graph_mint
        self._default_graph_doc = default_graph_doc
        self._default_graph_yaml = default_graph_yaml
        self._clock = clock

    def sweep(self) -> None:
        """One complete reconciliation pass over every not-yet-judged proposal of a
        delivered chunk. An empty candidate set ends the pass immediately
        — no default-graph resolution, which the loop below would otherwise repeat
        for nothing on every steady-state tick. A proposal whose own ``data`` fails to
        parse or is missing a field it needs is recorded unresolved rather than raised, so
        one malformed row never wedges every proposal behind it (``bzh:crash-exemptions-hub``
        §The delivery-materialization sweep). One aggregate INFO summary per pass
        (``bzh:structlog-logging``)."""
        proposals = self._delivery.unmaterialized_proposals()
        if not proposals:
            _log.info("work item materialization sweep completed", created=0, updated=0, unresolved=0, deferred=0)
            return
        # Resolved once per pass, not once per create-kind proposal —
        # invariant across one pass, since nothing inside the loop mints or retires a graph.
        default_graph = self._graph_mint.ensure_default_or_none(
            self._default_graph_doc, definition_yaml=self._default_graph_yaml
        )
        created = updated = unresolved = deferred = 0
        for row in proposals:
            outcome = self._materialize_one(row, default_graph)
            if outcome is WorkItemMaterializationOutcome.CREATED:
                created += 1
            elif outcome is WorkItemMaterializationOutcome.UPDATED:
                updated += 1
            elif outcome is WorkItemMaterializationOutcome.UNRESOLVED:
                unresolved += 1
            else:
                deferred += 1
        _log.info(
            "work item materialization sweep completed",
            created=created,
            updated=updated,
            unresolved=unresolved,
            deferred=deferred,
        )

    def _materialize_one(
        self, row: StampedWorkItemProposal, default_graph: Graph | None
    ) -> WorkItemMaterializationOutcome | None:
        proposal = parse_proposal(row)
        if isinstance(proposal, CreateProposal):
            return self._materialize_create(row, proposal, default_graph)
        if isinstance(proposal, UpdateProposal):
            return self._materialize_update(row, proposal)
        if proposal.malformed:
            _log.warning("work item proposal has malformed data", proposal_id=row.proposal_id, error=proposal.reason)
        return self._record_unresolved(row.proposal_id, proposal)

    def _materialize_create(
        self, row: StampedWorkItemProposal, proposal: CreateProposal, default_graph: Graph | None
    ) -> WorkItemMaterializationOutcome | None:
        """Always the reserved hub source. ``None`` means a transient failure — the
        default graph was retired (resolved once for the whole pass), or
        an out-of-band ingest pre-empted the allocated ref — left unjudged for the next
        pass, not recorded terminal."""
        if default_graph is None:
            return None
        try:
            minted = self._edits.materialize_create(
                row.proposal_id,
                title=proposal.text.title,
                body=proposal.text.body,
                author=proposal.author,
                stated_priority=proposal.stated_priority,
                graph=default_graph,
            )
        except IngestConflict:
            return None
        return WorkItemMaterializationOutcome.CREATED if minted else None

    def _materialize_update(
        self, row: StampedWorkItemProposal, proposal: UpdateProposal
    ) -> WorkItemMaterializationOutcome | None:
        """Resolves only through a source that implements the editor capability —
        today the hub source alone. A store write that lands nothing means a closure raced
        in since the read, left for the next pass to judge."""
        pointer = proposal.pointer
        editable = self._work_sources.editor(pointer.source) is not None
        item = self._items.get(pointer.source, pointer.ref) if editable else None
        refusal = judge_update_target(item, pointer=pointer, editable=editable)
        if refusal is not None:
            return self._record_unresolved(row.proposal_id, refusal)
        updated = self._items.materialize_update(
            proposal_id=row.proposal_id,
            source=pointer.source,
            ref=pointer.ref,
            evidence=proposal.evidence,
            at=self._clock.now(),
        )
        return WorkItemMaterializationOutcome.UPDATED if updated else None

    def _record_unresolved(self, proposal_id: str, judgment: Unresolvable) -> WorkItemMaterializationOutcome:
        self._delivery.record_work_item_materialization(
            proposal_id,
            outcome=WorkItemMaterializationOutcome.UNRESOLVED,
            pointer=judgment.pointer,
            reason=judgment.reason,
            at=self._clock.now(),
        )
        return WorkItemMaterializationOutcome.UNRESOLVED
