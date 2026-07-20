"""Chunk build-property edits — graph, model, and intended migration
(issue #27, widened by issue #120, per-field redesign by issue #124).

Ingest pins a chunk's workflow graph and model at mint (``ingest.py``); the not-ready
resting state issue #26 opens was the first window to change either before an agent
picks the chunk up. Issue #120 widens that window through promote: a chunk that has
left ``not_ready`` but sits ``ready`` with no live route is still editable — the wrong
graph is often noticed only after promote, with no runner anywhere near the chunk yet.
Issue #124 adds a third editable field, ``intended_migration``, whose window does not
match ``graph_id``/``model`` at all: it is editable at any non-terminal status,
``not_ready``/``ready`` included, not just once a chunk is claimed. It is *consulted*
only when a transition applies — which implies a claimed, progressing chunk — which is
why it complements rather than replaces the pre-claim ``graph_id``/``model`` repin.
Because the three fields no longer share one admit set, editability is validated **per
field** rather than once for the whole request — see :data:`_FIELD_WINDOW` and
:meth:`EditService.edit`.

All three edits are plain column overwrites, not append-only facts —
``bzh:facts-not-status`` governs *status derivation*, not every mutable field, and
``graph_id`` was already a mint-time column with no fact log behind it; ``model`` and
``intended_migration`` follow the same shape.

Widening the admit set to ``ready`` opens the edit window onto the same chunk a
runner's claim (:class:`~blizzard.hub.domain.claim.ClaimService`) can land against
concurrently — both are now check-then-act sequences over "does this chunk have a
live route", and an unguarded pair is a torn read: the edit's status check could pass
just before a claim lands, then write against a chunk that is now leased. Rather than
inventing a second synchronization mechanism, this service is handed the **same**
in-process lock :class:`~blizzard.hub.domain.claim.ClaimService` serializes its own
check-live-route/record-route CAS with (one lock per hub, injected at the composition
root — ``bzh:dependency-injection``): a claim and an edit racing the same chunk now
resolve to exactly one of "the edit sees the live route and 409s" or "the edit's write
already landed before the claim acquired the lock, and the claim proceeds after it" —
never a mix of both. ``intended_migration``'s own window never hinges on the live-route
check, so it never races a claim the same way, but it shares the lock anyway — one
edit-time invariant, one lock.

Holds the *write* chunk repository (``bzh:controller-read-only``); the route resolves the
chunk (and, for a graph or intended-migration edit, the target
:class:`~blizzard.hub.domain.graph.Graph` — ``bzh:domain-takes-objects``) and delegates
here.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from blizzard.hub.domain.graph import Graph, IReadGraphRepository
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    ChunkStatus,
    IntendedMigration,
    IWriteChunkRepository,
    MigrationMode,
    derive_chunk_status,
)


class _UnsetType(Enum):
    """The type of :data:`UNSET` — a single-member enum, not a plain class, so
    ``is``/``is not`` comparisons against it narrow a ``T | _UnsetType`` union for
    pyright (identity narrowing on a bare class instance is not reliably supported;
    on an enum literal it is)."""

    TOKEN = 0


#: Sentinel marking a :class:`ChunkEdit` field as *absent* from the request — "leave
#: this field unchanged" — distinct from ``None``, which for ``intended_migration``
#: means "clear the intent". A field carrying its type's own falsy value (``""``,
#: ``0``) must still be distinguishable from "not supplied", so ``UNSET`` is its own
#: singleton rather than reusing ``None``.
UNSET: Final = _UnsetType.TOKEN

#: The admit set ``graph_id``/``model`` have held since issue #120 — editable while
#: resting ``not_ready`` (issue #27's original window) or ``ready`` with no live
#: route; every other status means a runner has (or had) the chunk and the pin is
#: sealed.
_PRE_CLAIM_WINDOW = frozenset({ChunkStatus.NOT_READY, ChunkStatus.READY})

#: ``intended_migration``'s window (issue #124) — editable at any non-terminal status,
#: ``not_ready``/``ready`` included: a chunk sitting ``not_ready``, ``ready``,
#: ``running``, ``delivering``, ``waiting_on_human``, ``needs_human``, or ``paused``
#: may have its migration intent set, overwritten, or cleared. Closed at
#: ``done``/``stopped`` — there is no future transition left to consult it. Setting it
#: pre-claim is legitimate (an operator queuing a migration before a runner ever picks
#: the chunk up); it is simply *consulted* only once a claimed chunk actually reaches a
#: transition.
_INTENDED_MIGRATION_WINDOW = frozenset(ChunkStatus) - frozenset({ChunkStatus.DONE, ChunkStatus.STOPPED})

#: Per-field editable-status sets (issue #124) — the redesign this module docstring
#: describes. Keyed by the same field names :class:`ChunkEdit` carries.
_FIELD_WINDOW: Final[dict[str, frozenset[ChunkStatus]]] = {
    "graph_id": _PRE_CLAIM_WINDOW,
    "model": _PRE_CLAIM_WINDOW,
    "intended_migration": _INTENDED_MIGRATION_WINDOW,
}


class ChunkNotEditable(Exception):
    """An edit supplied a field outside *that field's* editable window (issue #124).

    Carries the offending ``field`` alongside the chunk's current ``status`` — a
    mixed-field request can be refused on any one of its fields, so the caller needs
    to know which."""

    def __init__(self, chunk_id: str, status: ChunkStatus, field_name: str) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, {field_name} is not editable at this status")
        self.chunk_id = chunk_id
        self.status = status
        self.field = field_name


class TargetGraphRetired(Exception):
    """A graph edit named a graph that has since been retired (issue #101)."""

    def __init__(self, graph_id: str) -> None:
        super().__init__(f"graph {graph_id} is retired and cannot receive new work")
        self.graph_id = graph_id


class MigrationTargetIsCurrentPin(Exception):
    """An intended migration's target graph is the chunk's own current pin (issue #124).

    Migrating a chunk onto the graph it is already pinned to is a no-op the operator
    almost certainly didn't mean — refused at request time rather than silently
    accepted and never firing anything different at consult time."""

    def __init__(self, graph_id: str) -> None:
        super().__init__(f"graph {graph_id} is the chunk's current graph pin, not a migration target")
        self.graph_id = graph_id


class ForcedNodeUnknown(Exception):
    """A ``forced`` intended migration named a node absent from its target graph (issue #124).

    Refused at request time — left unchecked, ``landing_node``'s entry-node fallback
    would silently reset the chunk to the target's entry node instead."""

    def __init__(self, node_name: str | None, graph_id: str) -> None:
        super().__init__(f"node {node_name!r} does not exist on graph {graph_id}")
        self.node_name = node_name
        self.graph_id = graph_id


@dataclass(frozen=True)
class ChunkEdit:
    """The fields a single edit request supplies (issue #124).

    Each field defaults to :data:`UNSET` — "not supplied, leave unchanged" — so a
    caller can request one field, two, or all three in a single all-or-nothing
    :meth:`EditService.edit` call. ``intended_migration`` additionally accepts
    ``None`` (distinct from ``UNSET``) to mean "clear the standing intent"."""

    graph_id: str | _UnsetType = field(default=UNSET)
    model: str | _UnsetType = field(default=UNSET)
    intended_migration: IntendedMigration | None | _UnsetType = field(default=UNSET)


class EditService:
    """Edit a chunk's graph, model, or intended-migration selection (issue #27, #120, #124)."""

    def __init__(
        self,
        *,
        chunks: IWriteChunkRepository,
        graphs: IReadGraphRepository,
        claim_lock: threading.Lock,
    ) -> None:
        self._chunks = chunks
        self._graphs = graphs
        # The same lock ClaimService serializes its claim CAS with — see the module
        # docstring's race-atomicity note (issue #120).
        self._claim_lock = claim_lock

    def set_graph(self, chunk: Chunk, *, graph: Graph) -> None:
        """Repin the chunk to ``graph`` — a thin wrapper over :meth:`edit` (issue #124)."""
        self.edit(chunk, ChunkEdit(graph_id=graph.graph_id), graph_target=graph)

    def set_model(self, chunk: Chunk, *, model: str) -> None:
        """Repin the chunk's model — a thin wrapper over :meth:`edit` (issue #124)."""
        self.edit(chunk, ChunkEdit(model=model))

    def edit(
        self,
        chunk: Chunk,
        edit: ChunkEdit,
        *,
        graph_target: Graph | None = None,
        migration_target: Graph | None = None,
    ) -> None:
        """Apply every field ``edit`` supplies, all-or-nothing (issue #124).

        Under the shared claim lock: every supplied field is validated first — its
        own editable-status window (:data:`_FIELD_WINDOW`), and, for a supplied
        non-``None`` ``intended_migration``, the semantic checks against
        ``migration_target`` and the chunk's current pin. If any field is refused, this
        raises and writes **nothing** — a mixed body is never partially applied.
        Only once every supplied field has passed does it write them.

        ``graph_target``/``migration_target`` are the resolved
        :class:`~blizzard.hub.domain.graph.Graph` a supplied ``graph_id`` /
        non-``None`` ``intended_migration`` targets, respectively — **separately**
        resolved and separately checked, one per field, even though a single request
        can name both fields at once with two different graphs. Collapsing them onto
        one shared graph would let one field's retirement check validate the *other*
        field's target — a retired ``graph_id`` slipping past its own
        :class:`TargetGraphRetired` check because the request's ``intended_migration``
        happened to name a different, non-retired graph. The controller resolves each
        independently (``bzh:domain-takes-objects``); this service takes no graph
        repository beyond the retirement check it already held.
        """
        graph_id = edit.graph_id
        model = edit.model
        intended_migration = edit.intended_migration

        with self._claim_lock:
            facts = self._chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
            status = derive_chunk_status(facts)

            if graph_id is not UNSET:
                self._require_editable(chunk.chunk_id, status, "graph_id")
                if graph_target is not None and self._graphs.is_retired(graph_target.graph_id):
                    raise TargetGraphRetired(graph_target.graph_id)

            if model is not UNSET:
                self._require_editable(chunk.chunk_id, status, "model")

            if intended_migration is not UNSET:
                self._require_editable(chunk.chunk_id, status, "intended_migration")
                if intended_migration is not None:
                    self._require_valid_migration_target(chunk, intended_migration, migration_target)

            if graph_id is not UNSET:
                self._chunks.set_graph(chunk.chunk_id, graph_id=graph_id)
            if model is not UNSET:
                self._chunks.set_model(chunk.chunk_id, model=model)
            if intended_migration is not UNSET:
                self._chunks.set_intended_migration(chunk.chunk_id, intended=intended_migration)

    def _require_valid_migration_target(
        self, chunk: Chunk, intended: IntendedMigration, target_graph: Graph | None
    ) -> None:
        """The request-time semantic refusals for a non-``None`` intended migration
        (issue #124 §5): a retired target, a target that is already the chunk's own
        pin, and — for ``forced`` — a named node absent from the target. Field-shape
        mismatches (``node_name`` with ``auto`` / missing with ``forced``) are the
        controller/wire's 422 concern, not this service's."""
        assert target_graph is not None, "an intended-migration edit requires its resolved target graph"
        if self._graphs.is_retired(target_graph.graph_id):
            raise TargetGraphRetired(target_graph.graph_id)
        if target_graph.graph_id == chunk.graph_id:
            raise MigrationTargetIsCurrentPin(target_graph.graph_id)
        if intended.mode is MigrationMode.FORCED and target_graph.node_by_name(intended.node_name or "") is None:
            raise ForcedNodeUnknown(intended.node_name, target_graph.graph_id)

    def _require_editable(self, chunk_id: str, status: ChunkStatus, field_name: str) -> None:
        if status not in _FIELD_WINDOW[field_name]:
            raise ChunkNotEditable(chunk_id, status, field_name)
