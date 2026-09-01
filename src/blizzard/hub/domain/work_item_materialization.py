"""The delivery-materialization sweep (blizzard#366): a delivered chunk's accumulated
``work_item_proposals`` rows become real work items. Eventually convergent, never
atomic with the landing (D1) — domain layer only (``bzh:domain-core``): every
collaborator is either an injected Protocol or another domain-layer service
(:class:`~blizzard.hub.domain.work_items.WorkItemEditService`,
:class:`~blizzard.hub.domain.graph_authoring.GraphMintService`), never an adapter, so
:meth:`WorkItemMaterializationReconciler.sweep` is one complete, directly-callable step
(``bzh:steppable-loop``)."""

from __future__ import annotations

import json

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.chunks.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.graph import GraphDoc
from blizzard.hub.domain.graph_authoring import GraphMintService
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import (
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
        delivered chunk. A proposal whose own ``data`` fails to parse or is missing a
        field it needs is recorded unresolved rather than raised, so one malformed row
        never wedges every proposal behind it (``bzh:crash-exemptions-hub`` §The
        delivery-materialization sweep). One aggregate INFO summary per pass
        (``bzh:structlog-logging``)."""
        created = updated = unresolved = deferred = 0
        for row in self._delivery.unmaterialized_proposals():
            outcome = self._materialize_one(row)
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

    def _materialize_one(self, row: WorkItemProposalRow) -> WorkItemMaterializationOutcome | None:
        try:
            data = json.loads(row.data)
            if row.kind == "create":
                return self._materialize_create(row, data)
            return self._materialize_update(row, data)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            _log.warning("work item proposal has malformed data", proposal_id=row.proposal_id, error=str(exc))
            return self._record_unresolved(row.proposal_id, pointer=None, reason=f"malformed proposal data: {exc}")

    def _materialize_create(self, row: WorkItemProposalRow, data: dict) -> WorkItemMaterializationOutcome | None:
        """D7: always the reserved hub source. ``None`` means a transient failure — the
        default graph was retired mid-mint, or an out-of-band ingest pre-empted the
        allocated ref — left unjudged for the next pass, not recorded terminal."""
        if row.runner_id is None:
            return self._record_unresolved(
                row.proposal_id, pointer=None, reason="no proposing runner recorded for this proposal"
            )
        graph = self._graph_mint.ensure_default_or_none(
            self._default_graph_doc, definition_yaml=self._default_graph_yaml
        )
        if graph is None:
            return None
        author = WorkItemAuthor.fleet(runner_id=row.runner_id, chunk_id=row.chunk_id, node_name=row.node_name)
        try:
            minted = self._edits.materialize_create(
                row.proposal_id,
                title=data["title"],
                body=data["body"],
                author=author,
                stated_priority=data.get("stated_priority"),
                graph=graph,
            )
        except IngestConflict:
            return None
        return WorkItemMaterializationOutcome.CREATED if minted else None

    def _materialize_update(self, row: WorkItemProposalRow, data: dict) -> WorkItemMaterializationOutcome | None:
        """D6: resolves only through a source that implements the editor capability —
        today the hub source alone. Every other unresolvable case (nonexistent, closed,
        withdrawn) is the work item's own three named cases."""
        pointer = WorkRef(source=data["source"], ref=data["ref"])
        if self._work_sources.editor(pointer.source) is None:
            return self._record_unresolved(
                row.proposal_id, pointer=pointer, reason=f"source {pointer.source!r} has no editor"
            )
        item = self._items.get(pointer.source, pointer.ref)
        if item is None:
            return self._record_unresolved(row.proposal_id, pointer=pointer, reason="item does not exist")
        if item.closure is not None:
            return self._record_unresolved(row.proposal_id, pointer=pointer, reason=f"item is {item.closure.value}")
        updated = self._items.materialize_update(
            proposal_id=row.proposal_id,
            source=pointer.source,
            ref=pointer.ref,
            evidence=data["evidence"],
            at=self._clock.now(),
        )
        return WorkItemMaterializationOutcome.UPDATED if updated else None

    def _record_unresolved(
        self, proposal_id: str, *, pointer: WorkRef | None, reason: str
    ) -> WorkItemMaterializationOutcome:
        self._delivery.record_work_item_materialization(
            proposal_id,
            outcome=WorkItemMaterializationOutcome.UNRESOLVED,
            pointer=pointer,
            reason=reason,
            at=self._clock.now(),
        )
        return WorkItemMaterializationOutcome.UNRESOLVED
