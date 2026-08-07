"""Chunk build-property edits — graph, defaults, intended migration (issues #27, #124).

The fields do not share one admit set, so editability is validated **per field** — see
:data:`_FIELD_WINDOW`. An edit is a plain column overwrite: ``bzh:facts-not-status``
governs *status derivation*, not every mutable field. An edit and a claim are both
check-then-act over "does this chunk have a live route", so they share one lock (#120)."""

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


#: "Field absent from the request, leave it unchanged" — distinct from ``None``, which
#: means "clear it", and from a field's own falsy value.
UNSET: Final = _UnsetType.TOKEN

#: The unclaimed admit set — not "never claimed": see :class:`ChunkAlreadyMoved`.
_PRE_CLAIM_WINDOW = frozenset({ChunkStatus.NOT_READY, ChunkStatus.READY})

#: Closed at ``done``/``stopped`` — no future transition is left to consult the intent.
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

    Carries the offending ``field``: a mixed request is refused on any one of them."""

    def __init__(self, chunk_id: str, status: ChunkStatus, field_name: str) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, {field_name} is not editable at this status")
        self.chunk_id = chunk_id
        self.status = status
        self.field = field_name


class ChunkAlreadyMoved(Exception):
    """A graph re-pin named a chunk that has already moved (``bzh:migration-not-transition``)."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"chunk {chunk_id} has already moved — re-pin it with a migration, not an edit")
        self.chunk_id = chunk_id


class TargetGraphRetired(Exception):
    """A graph edit named a graph that has since been retired (issue #101)."""

    def __init__(self, graph_id: str) -> None:
        super().__init__(f"graph {graph_id} is retired and cannot receive new work")
        self.graph_id = graph_id


class MigrationTargetIsCurrentPin(Exception):
    """An intended migration's target graph is the chunk's own current pin (issue #124).

    A no-op intent, refused at request time rather than silently accepted."""

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
    """The fields a single all-or-nothing edit request supplies (issue #124).

    ``intended_migration`` and ``default_effort`` accept ``None`` to mean "clear it";
    an empty ``default_model`` list is the same clear."""

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

        Under the shared claim lock, every supplied field is validated before anything is
        written, so a refusal writes nothing; each target graph is checked separately —
        tests/test_edit_service.py::test_edit_graph_id_retirement_check_is_not_bypassed_by_a_different_migration_target"""
        graph_id = edit.graph_id
        default_model = edit.default_model
        default_effort = edit.default_effort
        intended_migration = edit.intended_migration

        with self._claim_lock:
            facts = self._chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
            status = derive_chunk_status(facts)

            if graph_id is not UNSET:
                self._require_editable(chunk.chunk_id, status, "graph_id")
                if facts.current_node_id() is not None:
                    raise ChunkAlreadyMoved(chunk.chunk_id)
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
                # One write for the pair, so an edit naming only one of them must carry
                # the chunk's current value for the other rather than clearing it.
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
