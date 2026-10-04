"""Chunk build-property edits — graph, defaults, intended migration.

Editability is validated **per field**: each field reads its own
:class:`~blizzard.hub.domain.chunk.model.ChunkVerb` row (:data:`_FIELD_VERB`). An edit is a plain column
overwrite, and shares one lock with a claim since both check for a live route. :func:`plan_edit` decides;
:class:`EditService` reads, locks, and writes what it decided."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from blizzard.foundation.chunk_migration import MigrationMode
from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb, IntendedMigration
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.record import IWriteChunkRecordRepository
from blizzard.hub.domain.graph.harnesses import validated_harnesses
from blizzard.hub.domain.graph.model import Graph, GraphStanding, IReadGraphRepository
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

#: The verb each field :class:`ChunkEdit` carries is admitted by — its status window.
_FIELD_VERB: Mapping[str, ChunkVerb] = MappingProxyType(
    {
        "graph_id": ChunkVerb.EDIT_GRAPH_PIN,
        "default_model": ChunkVerb.EDIT_DEFAULTS,
        "default_effort": ChunkVerb.EDIT_DEFAULTS,
        "default_harnesses": ChunkVerb.EDIT_DEFAULTS,
        "intended_migration": ChunkVerb.EDIT_INTENDED_MIGRATION,
    }
)


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


class BlankEditValue(ValueError):
    """An edit supplied a value that is blank once trimmed; ``field_name`` names it."""

    def __init__(self, detail: str, *, field_name: str) -> None:
        super().__init__(detail)
        self.field_name = field_name


def filled(value: str, field_name: str) -> str:
    """``value`` trimmed; a value blank once trimmed is refused (:class:`BlankEditValue`)."""
    text = value.strip()
    if not text:
        raise BlankEditValue(f"{field_name} must not be blank", field_name=field_name)
    return text


def filled_entries(entries: list[str], field_name: str) -> list[str]:
    """Every entry trimmed; any entry blank once trimmed refuses the whole list
    (:class:`BlankEditValue`). An empty list is the clear, not a blank."""
    trimmed = [entry.strip() for entry in entries]
    if any(not entry for entry in trimmed):
        raise BlankEditValue(f"{field_name} entries must not be blank", field_name=field_name)
    return trimmed


@domain_model
@dataclass(frozen=True)
class ChunkDefaults:
    """The default model, effort, and harnesses — written as one trio."""

    model: list[str]
    effort: str | None
    harnesses: list[str]


@domain_model
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

    def supplies_defaults(self) -> bool:
        """Whether any of the default trio is supplied."""
        return not (self.default_model is UNSET and self.default_effort is UNSET and self.default_harnesses is UNSET)

    def defaults_over(self, current: Chunk, *, harnesses: list[str] | UnsetType = UNSET) -> ChunkDefaults:
        """The trio this edit writes: each supplied field over ``current``'s value, so an edit
        naming one of them never clears the other two. ``harnesses`` is the validated list
        standing in for the supplied ``default_harnesses``."""
        chosen_harnesses = self.default_harnesses if harnesses is UNSET else harnesses
        return ChunkDefaults(
            model=list(current.default_model) if self.default_model is UNSET else self.default_model,
            effort=current.default_effort if self.default_effort is UNSET else self.default_effort,
            harnesses=list(current.default_harnesses) if chosen_harnesses is UNSET else chosen_harnesses,
        )


@domain_model
@dataclass(frozen=True)
class EditPlan:
    """:func:`plan_edit`'s decision: what to write, each part ``None``/``UNSET`` when untouched."""

    #: The new graph pin; ``None`` when not supplied or naming the current pin (a no-op, not a migration).
    graph_id: str | None
    defaults: ChunkDefaults | None
    intended_migration: IntendedMigration | None | UnsetType


def _require_editable(chunk_id: str, facts: ChunkFacts, field_name: str) -> None:
    if not facts.admits(_FIELD_VERB[field_name]):
        raise ChunkNotEditable(chunk_id, facts.status(), field_name)


def require_valid_migration_target(
    chunk: Chunk, intended: IntendedMigration, target_graph: Graph, *, retired: bool
) -> None:
    """The refusals for a non-``None`` intended migration: a retired target, a target that is
    already the chunk's own pin, and — for ``forced`` — a named node absent from the target.
    Field-shape mismatches (``node_name`` with ``auto`` / missing with ``forced``) are the
    wire's concern."""
    GraphStanding(target_graph, retired=retired).require_targetable()
    if target_graph.graph_id == chunk.graph_id:
        raise MigrationTargetIsCurrentPin(target_graph.graph_id)
    if intended.mode is MigrationMode.FORCED and target_graph.node_by_name(intended.node_name or "") is None:
        raise ForcedNodeUnknown(intended.node_name, target_graph.graph_id)


def plan_edit(
    chunk: Chunk,
    facts: ChunkFacts,
    edit: ChunkEdit,
    *,
    graph_target: Graph | None = None,
    graph_target_retired: bool = False,
    migration_target: Graph | None = None,
    migration_target_retired: bool = False,
) -> EditPlan:
    """Decide an all-or-nothing edit of ``chunk`` at ``facts``: every supplied field is
    validated before anything is planned, in field order, so a refusal writes nothing.

    A graph pin edit names its loaded target graph and the target's retirement; each target is checked
    on its own, so a valid migration target never stands in for a retired pin target."""
    graph_id: str | None = None
    if edit.graph_id is not UNSET:
        if graph_target is None or graph_target.graph_id != edit.graph_id:
            raise ValueError("a graph pin edit requires its loaded target graph")
        _require_editable(chunk.chunk_id, facts, "graph_id")
        if facts.current_node_id() is not None:
            raise ChunkAlreadyMoved(chunk.chunk_id)
        GraphStanding(graph_target, retired=graph_target_retired).require_targetable()
        graph_id = None if edit.graph_id == chunk.graph_id else edit.graph_id
    if edit.default_model is not UNSET:
        _require_editable(chunk.chunk_id, facts, "default_model")
    if edit.default_effort is not UNSET:
        _require_editable(chunk.chunk_id, facts, "default_effort")
    harnesses: list[str] | UnsetType = UNSET
    if edit.default_harnesses is not UNSET:
        _require_editable(chunk.chunk_id, facts, "default_harnesses")
        harnesses = validated_harnesses(edit.default_harnesses)
    if edit.intended_migration is not UNSET:
        _require_editable(chunk.chunk_id, facts, "intended_migration")
        if edit.intended_migration is not None:
            assert migration_target is not None, "an intended-migration edit requires its resolved target graph"
            require_valid_migration_target(
                chunk, edit.intended_migration, migration_target, retired=migration_target_retired
            )
    return EditPlan(
        graph_id=graph_id,
        defaults=edit.defaults_over(chunk, harnesses=harnesses) if edit.supplies_defaults() else None,
        intended_migration=edit.intended_migration,
    )


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
        """Apply every field ``edit`` supplies, all-or-nothing, as :func:`plan_edit` decides.

        The facts and the record are re-read under the shared row lock, so a concurrent edit landing
        before it neither loses its write to the trio's carry-forward nor skews the pin check."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            # A `None` load means gone under this lock — refuse rather than substitute a
            # synthetic status, mirroring `DeleteService.delete`/`DependencyService.declare`.
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            current = handle.record(chunk.chunk_id)
            if current is None:
                raise ChunkNotFound(chunk.chunk_id)
            plan = plan_edit(
                current,
                facts,
                edit,
                graph_target=graph_target,
                graph_target_retired=graph_target is not None and self._graphs.is_retired(graph_target.graph_id),
                migration_target=migration_target,
                migration_target_retired=migration_target is not None
                and self._graphs.is_retired(migration_target.graph_id),
            )
            if plan.graph_id is not None:
                self._record.set_graph_locked(handle, chunk.chunk_id, graph_id=plan.graph_id)
            if plan.defaults is not None:
                self._record.set_defaults_locked(
                    handle,
                    chunk.chunk_id,
                    default_model=plan.defaults.model,
                    default_effort=plan.defaults.effort,
                    default_harnesses=plan.defaults.harnesses,
                )
            if plan.intended_migration is not UNSET:
                self._record.set_intended_migration_locked(handle, chunk.chunk_id, intended=plan.intended_migration)
