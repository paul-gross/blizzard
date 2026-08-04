"""Chunk build-property edits — graph, default model/effort, and intended migration
(issues #27, #120, #124, #144).

Ingest pins a chunk's workflow graph at mint (``ingest.py``); this service changes it
afterward. ``graph_id`` and the ``default_model``/``default_effort`` pair are editable
**pre-claim** — while the chunk rests ``not_ready``, or sits ``ready`` with no live
route, since the wrong graph is often noticed only after promote and with no runner
anywhere near the chunk yet. ``intended_migration`` (issue #124) is editable at any
non-terminal status instead: it is *consulted* only when a transition applies — which
implies a claimed, progressing chunk — so it complements rather than replaces the
pre-claim ``graph_id`` repin. Because the fields do not share one admit set,
editability is validated **per field** rather than once for the whole request — see
:data:`_FIELD_WINDOW` and :meth:`EditService.edit`.

Every edit here is a plain column overwrite, not an append-only fact —
``bzh:facts-not-status`` governs *status derivation*, not every mutable field.

An edit and a claim are both check-then-act sequences over "does this chunk have a live
route", so this service is handed the **same** in-process lock
:class:`~blizzard.hub.domain.claim.ClaimService` serializes its own CAS with (one lock
per hub, injected at the composition root — ``bzh:dependency-injection``, issue #120);
see that module's docstring for the race it closes. ``intended_migration``'s own window
never hinges on the live-route check, so it never races a claim the same way, but it
shares the lock anyway — one edit-time invariant, one lock.

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

#: The pre-claim admit set (issues #27, #120) — editable while resting ``not_ready``
#: or ``ready`` with no live route; every other status means a runner has (or had) the
#: chunk and the pin is sealed.
_PRE_CLAIM_WINDOW = frozenset({ChunkStatus.NOT_READY, ChunkStatus.READY})

#: ``intended_migration``'s window (issue #124) — editable at any non-terminal status,
#: ``not_ready``/``ready`` included: setting it pre-claim is legitimate (an operator
#: queuing a migration before a runner ever picks the chunk up). Closed at
#: ``done``/``stopped`` — there is no future transition left to consult it.
_INTENDED_MIGRATION_WINDOW = frozenset(ChunkStatus) - frozenset({ChunkStatus.DONE, ChunkStatus.STOPPED})

#: Per-field editable-status sets (issue #124), keyed by the same field names
#: :class:`ChunkEdit` carries.
_FIELD_WINDOW: Final[dict[str, frozenset[ChunkStatus]]] = {
    "graph_id": _PRE_CLAIM_WINDOW,
    "default_model": _PRE_CLAIM_WINDOW,
    "default_effort": _PRE_CLAIM_WINDOW,
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
    caller can request one field or every one of them in a single all-or-nothing
    :meth:`EditService.edit` call. ``intended_migration`` and ``default_effort``
    additionally accept ``None`` (distinct from ``UNSET``) to mean "clear it"; an empty
    ``default_model`` list is the same "express no preference" clear."""

    graph_id: str | _UnsetType = field(default=UNSET)
    default_model: list[str] | _UnsetType = field(default=UNSET)
    default_effort: str | None | _UnsetType = field(default=UNSET)
    intended_migration: IntendedMigration | None | _UnsetType = field(default=UNSET)


class EditService:
    """Edit a chunk's graph, default model/effort, or intended-migration selection
    (issues #27, #120, #124, #144)."""

    def __init__(
        self,
        *,
        chunks: IWriteChunkRepository,
        graphs: IReadGraphRepository,
        claim_lock: threading.Lock,
    ) -> None:
        self._chunks = chunks
        self._graphs = graphs
        # The same lock ClaimService serializes its claim CAS with (issue #120).
        self._claim_lock = claim_lock

    def set_graph(self, chunk: Chunk, *, graph: Graph) -> None:
        """Repin the chunk to ``graph`` — a thin wrapper over :meth:`edit` (issue #124)."""
        self.edit(chunk, ChunkEdit(graph_id=graph.graph_id), graph_target=graph)

    def set_defaults(self, chunk: Chunk, *, default_model: list[str], default_effort: str | None) -> None:
        """Repin the chunk's default model/effort — a thin wrapper over :meth:`edit`
        (issues #124, #144)."""
        self.edit(chunk, ChunkEdit(default_model=default_model, default_effort=default_effort))

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
        resolved and separately checked, one per field, so one field's retirement check
        can never validate the *other* field's target — pinned by
        tests/test_edit_service.py::test_edit_graph_id_retirement_check_is_not_bypassed_by_a_different_migration_target
        The controller resolves each independently (``bzh:domain-takes-objects``); this
        service takes no graph repository beyond the retirement check it already held.
        """
        graph_id = edit.graph_id
        default_model = edit.default_model
        default_effort = edit.default_effort
        intended_migration = edit.intended_migration

        with self._claim_lock:
            facts = self._chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
            status = derive_chunk_status(facts)

            if graph_id is not UNSET:
                self._require_editable(chunk.chunk_id, status, "graph_id")
                if graph_target is not None and self._graphs.is_retired(graph_target.graph_id):
                    raise TargetGraphRetired(graph_target.graph_id)

            if default_model is not UNSET:
                self._require_editable(chunk.chunk_id, status, "default_model")

            if default_effort is not UNSET:
                self._require_editable(chunk.chunk_id, status, "default_effort")

            if intended_migration is not UNSET:
                self._require_editable(chunk.chunk_id, status, "intended_migration")
                if intended_migration is not None:
                    self._require_valid_migration_target(chunk, intended_migration, migration_target)

            if graph_id is not UNSET:
                self._chunks.set_graph(chunk.chunk_id, graph_id=graph_id)
            if default_model is not UNSET or default_effort is not UNSET:
                # One write for the pair (``set_defaults`` takes both), so an edit naming
                # only one of them carries the chunk's *current* value for the other
                # rather than clearing it — "not supplied" stays "leave unchanged" even
                # though the two share a write.
                self._chunks.set_defaults(
                    chunk.chunk_id,
                    default_model=list(chunk.default_model) if default_model is UNSET else default_model,
                    default_effort=chunk.default_effort if default_effort is UNSET else default_effort,
                )
            if intended_migration is not UNSET:
                self._chunks.set_intended_migration(chunk.chunk_id, intended=intended_migration)

    def _require_valid_migration_target(
        self, chunk: Chunk, intended: IntendedMigration, target_graph: Graph | None
    ) -> None:
        """The request-time semantic refusals for a non-``None`` intended migration
        (issue #124 §5): a retired target, a target that is already the chunk's own
        pin, and — for ``forced`` — a named node absent from the target. Field-shape
        mismatches (``node_name`` with ``auto`` / missing with ``forced``) are the
        wire's concern, not this service's."""
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
