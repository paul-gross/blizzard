"""Chunk build-property edits — graph, defaults, intended migration.

The fields do not share one admit set, so editability is validated **per field** — see
:data:`_FIELD_WINDOW`. An edit is a plain column overwrite: ``bzh:facts-not-status``
governs *status derivation*, not every mutable field. An edit and a claim are both
check-then-act over "does this chunk have a live route", so they share one lock (#120)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.hub.domain.chunks.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunks.record import IWriteChunkRecordRepository
from blizzard.hub.domain.errors import ChunkNotFound
from blizzard.hub.domain.graph import Graph, IReadGraphRepository
from blizzard.hub.domain.harnesses import validated_harnesses
from blizzard.hub.domain.work import Chunk, IntendedMigration, MigrationMode


class UnsetType(Enum):
    """The type of :data:`UNSET` — a single-member enum, not a plain class, so
    ``is``/``is not`` comparisons against it narrow a ``T | UnsetType`` union for
    pyright (identity narrowing on a bare class instance is not reliably supported;
    on an enum literal it is)."""

    TOKEN = 0


#: "Field absent from the request, leave it unchanged" — distinct from ``None``, which
#: means "clear it", and from a field's own falsy value.
UNSET: Final = UnsetType.TOKEN

#: Closed at ``done``/``stopped`` — no future transition is left to consult the intent.
_INTENDED_MIGRATION_WINDOW = frozenset(ChunkStatus) - frozenset({ChunkStatus.DONE, ChunkStatus.STOPPED})

#: Per-field editable-status sets, keyed by the same field names
#: :class:`ChunkEdit` carries.
_FIELD_WINDOW: Final[dict[str, frozenset[ChunkStatus]]] = {
    "graph_id": PRE_CLAIM_STATUSES,
    "default_model": PRE_CLAIM_STATUSES,
    "default_effort": PRE_CLAIM_STATUSES,
    "default_harnesses": PRE_CLAIM_STATUSES,
    "intended_migration": _INTENDED_MIGRATION_WINDOW,
}


class ChunkNotEditable(Exception):
    """An edit supplied a field outside *that field's* editable window.

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
    """A graph edit named a graph that has since been retired."""

    def __init__(self, graph_id: str) -> None:
        super().__init__(f"graph {graph_id} is retired and cannot receive new work")
        self.graph_id = graph_id


class MigrationTargetIsCurrentPin(Exception):
    """An intended migration's target graph is the chunk's own current pin.

    A no-op intent, refused at request time rather than silently accepted."""

    def __init__(self, graph_id: str) -> None:
        super().__init__(f"graph {graph_id} is the chunk's current graph pin, not a migration target")
        self.graph_id = graph_id


class ForcedNodeUnknown(Exception):
    """A ``forced`` intended migration named a node absent from its target graph.

    Refused at request time — left unchecked, ``landing_node``'s entry-node fallback
    would silently reset the chunk to the target's entry node instead."""

    def __init__(self, node_name: str | None, graph_id: str) -> None:
        super().__init__(f"node {node_name!r} does not exist on graph {graph_id}")
        self.node_name = node_name
        self.graph_id = graph_id


@dataclass(frozen=True)
class ChunkEdit:
    """The fields a single all-or-nothing edit request supplies.

    ``intended_migration`` and ``default_effort`` accept ``None`` to mean "clear it";
    an empty ``default_model``/``default_harnesses`` list is the same clear."""

    graph_id: str | UnsetType = field(default=UNSET)
    default_model: list[str] | UnsetType = field(default=UNSET)
    default_effort: str | None | UnsetType = field(default=UNSET)
    default_harnesses: list[str] | UnsetType = field(default=UNSET)
    intended_migration: IntendedMigration | None | UnsetType = field(default=UNSET)


class EditService:
    """Edit a chunk's graph, default model/effort, or intended-migration selection."""

    def __init__(
        self,
        *,
        record: IWriteChunkRecordRepository,
        graphs: IReadGraphRepository,
        exclusive: IChunkExclusiveWrites,
    ) -> None:
        self._record = record
        self._graphs = graphs
        # The locked-transaction seam (``bzh:store-exclusive-write``) ClaimService's own
        # CAS shares — the same row lock, never an in-process lock.
        self._exclusive = exclusive

    def set_graph(self, chunk: Chunk, *, graph: Graph) -> None:
        """Repin the chunk to ``graph`` — a thin wrapper over :meth:`edit`."""
        self.edit(chunk, ChunkEdit(graph_id=graph.graph_id), graph_target=graph)

    def set_defaults(
        self,
        chunk: Chunk,
        *,
        default_model: list[str],
        default_effort: str | None,
        default_harnesses: list[str] | UnsetType = UNSET,
    ) -> None:
        """Repin the chunk's default model/effort/harnesses — a thin wrapper over
        :meth:`edit`."""
        self.edit(
            chunk,
            ChunkEdit(
                default_model=default_model,
                default_effort=default_effort,
                default_harnesses=default_harnesses,
            ),
        )

    def edit(
        self,
        chunk: Chunk,
        edit: ChunkEdit,
        *,
        graph_target: Graph | None = None,
        migration_target: Graph | None = None,
    ) -> None:
        """Apply every field ``edit`` supplies, all-or-nothing.

        Under the shared row lock (``bzh:store-exclusive-write``), every supplied field is
        validated before anything is written, so a refusal writes nothing; each target
        graph is checked separately —
        tests/test_edit_service.py::test_edit_graph_id_retirement_check_is_not_bypassed_by_a_different_migration_target"""
        graph_id = edit.graph_id
        default_model = edit.default_model
        default_effort = edit.default_effort
        default_harnesses = edit.default_harnesses
        intended_migration = edit.intended_migration

        with self._exclusive.locked([chunk.chunk_id]) as handle:
            # A `None` load means gone under this lock — refuse rather than substitute a
            # synthetic status, mirroring `DeleteService.delete`/`DependencyService.declare`.
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            status = facts.status()
            # Re-read fresh under the lock: a concurrent edit landing between the
            # caller's own load and this lock must not have its write silently lost by
            # the trio's carry-forward below, nor the pin check answered against a
            # ``graph_id`` that edit already changed.
            current = handle.record(chunk.chunk_id)
            if current is None:
                raise ChunkNotFound(chunk.chunk_id)

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

            if default_harnesses is not UNSET:
                self._require_editable(chunk.chunk_id, status, "default_harnesses")
                default_harnesses = validated_harnesses(default_harnesses)

            if intended_migration is not UNSET:
                self._require_editable(chunk.chunk_id, status, "intended_migration")
                if intended_migration is not None:
                    self._require_valid_migration_target(current, intended_migration, migration_target)

            if graph_id is not UNSET:
                self._record.set_graph_locked(handle, chunk.chunk_id, graph_id=graph_id)
            if default_model is not UNSET or default_effort is not UNSET or default_harnesses is not UNSET:
                # One write for the trio, so an edit naming only one of them must carry
                # the chunk's current value for the other two rather than clearing them
                # — ``current``, re-read under the lock, not the caller's possibly-stale
                # ``chunk``, so a concurrent single-field edit's own write is never
                # overwritten back to what it looked like before that edit landed.
                self._record.set_defaults_locked(
                    handle,
                    chunk.chunk_id,
                    default_model=list(current.default_model) if default_model is UNSET else default_model,
                    default_effort=current.default_effort if default_effort is UNSET else default_effort,
                    default_harnesses=list(current.default_harnesses)
                    if default_harnesses is UNSET
                    else default_harnesses,
                )
            if intended_migration is not UNSET:
                self._record.set_intended_migration_locked(handle, chunk.chunk_id, intended=intended_migration)

    def _require_valid_migration_target(
        self, chunk: Chunk, intended: IntendedMigration, target_graph: Graph | None
    ) -> None:
        """The request-time semantic refusals for a non-``None`` intended migration:
        a retired target, a target that is already the chunk's own
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
