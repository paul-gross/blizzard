"""The delivery-materialization sweep (blizzard#366): a delivered chunk's accumulated
``work_item_proposals`` rows become real work items. Eventually convergent, never
atomic with the landing (D1) — dependency-free (``bzh:domain-core``), every
collaborator an injected Protocol, so :meth:`WorkItemMaterializationReconciler.sweep`
is one complete, directly-callable step (``bzh:steppable-loop``)."""

from __future__ import annotations

import json

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.graph import GraphDoc
from blizzard.hub.domain.graph_authoring import DefaultGraphRetired, GraphMintService
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import (
    IWriteChunkRepository,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkItemMaterializationOutcome,
    WorkRef,
)
from blizzard.hub.domain.work_items import WorkItemEditService
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.work_item_materialization")


class WorkItemMaterializationReconciler:
    """Per not-yet-judged proposal of a delivered chunk (D2): mint a ``create``
    proposal into the hub source (D7), or append an ``update`` proposal's evidence to
    the item its pointer names (D6) — every proposal materializes, with no epoch filter
    (D3). Unresolvable is recorded with its reason and never fails the sweep; a
    transient failure (a retired default graph, a pre-empted ref) records nothing and
    is retried next pass."""

    def __init__(
        self,
        *,
        chunks: IWriteChunkRepository,
        items: IWriteWorkItemRepository,
        edits: WorkItemEditService,
        work_sources: IWorkSourceRegistry,
        graph_mint: GraphMintService,
        default_graph_doc: GraphDoc,
        default_graph_yaml: str,
        clock: IClock,
    ) -> None:
        self._chunks = chunks
        self._items = items
        self._edits = edits
        self._work_sources = work_sources
        self._graph_mint = graph_mint
        self._default_graph_doc = default_graph_doc
        self._default_graph_yaml = default_graph_yaml
        self._clock = clock

    def sweep(self) -> None:
        """One complete reconciliation pass over every not-yet-judged proposal of a
        delivered chunk. One aggregate INFO summary per pass (``bzh:structlog-logging``)."""
        created = updated = unresolved = skipped = 0
        for row in self._chunks.unmaterialized_proposals():
            data = json.loads(row.data)
            if row.kind == "create":
                outcome = self._materialize_create(row, data)
            else:
                outcome = self._materialize_update(row, data)
            if outcome is WorkItemMaterializationOutcome.CREATED:
                created += 1
            elif outcome is WorkItemMaterializationOutcome.UPDATED:
                updated += 1
            elif outcome is WorkItemMaterializationOutcome.UNRESOLVED:
                unresolved += 1
            else:
                skipped += 1
        _log.info(
            "work item materialization sweep completed",
            created=created,
            updated=updated,
            unresolved=unresolved,
            skipped=skipped,
        )

    def _materialize_create(
        self, row: WorkItemProposalRow, data: dict
    ) -> WorkItemMaterializationOutcome | None:
        """D7: always the reserved hub source. ``None`` means a transient failure
        (assumption 8) — left unjudged for the next pass, not recorded terminal."""
        if row.runner_id is None:
            self._record_unresolved(row.proposal_id, pointer=None, reason="no recorded proposer (D4)")
            return WorkItemMaterializationOutcome.UNRESOLVED
        try:
            graph = self._graph_mint.ensure_default(self._default_graph_doc, definition_yaml=self._default_graph_yaml)
        except DefaultGraphRetired:
            return None
        author = WorkItemAuthor.fleet(runner_id=row.runner_id, chunk_id=row.chunk_id, node_name=row.node_name)
        try:
            result = self._edits.materialize_create(
                row.proposal_id,
                title=data["title"],
                body=data["body"],
                author=author,
                stated_priority=data.get("stated_priority"),
                graph=graph,
            )
        except IngestConflict:
            return None
        return WorkItemMaterializationOutcome.CREATED if result is not None else None

    def _materialize_update(
        self, row: WorkItemProposalRow, data: dict
    ) -> WorkItemMaterializationOutcome | None:
        """D6: resolves only through a source that implements the editor capability —
        today the hub source alone. Every other unresolvable case (nonexistent, closed,
        withdrawn) is the work item's own three named cases."""
        pointer = WorkRef(source=data["source"], ref=data["ref"])
        if self._work_sources.editor(pointer.source) is None:
            self._record_unresolved(row.proposal_id, pointer=pointer, reason=f"source {pointer.source!r} has no editor")
            return WorkItemMaterializationOutcome.UNRESOLVED
        item = self._items.get(pointer.source, pointer.ref)
        if item is None:
            self._record_unresolved(row.proposal_id, pointer=pointer, reason="item does not exist")
            return WorkItemMaterializationOutcome.UNRESOLVED
        if item.closure is not None:
            self._record_unresolved(row.proposal_id, pointer=pointer, reason=f"item is {item.closure.value}")
            return WorkItemMaterializationOutcome.UNRESOLVED
        result = self._items.materialize_update(
            proposal_id=row.proposal_id,
            source=pointer.source,
            ref=pointer.ref,
            evidence=data["evidence"],
            at=self._clock.now(),
        )
        return WorkItemMaterializationOutcome.UPDATED if result is not None else None

    def _record_unresolved(self, proposal_id: str, *, pointer: WorkRef | None, reason: str) -> None:
        self._chunks.record_work_item_materialization(
            proposal_id,
            outcome=WorkItemMaterializationOutcome.UNRESOLVED,
            pointer=pointer,
            reason=reason,
            at=self._clock.now(),
        )
