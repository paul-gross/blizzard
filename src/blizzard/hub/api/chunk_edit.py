"""Reading a ``PATCH /chunks/{id}`` body as the domain edit it asks for."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status

from blizzard.hub.api.graph_names import graph_by_ref
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.chunk.model import Chunk, IntendedMigration
from blizzard.hub.domain.graph.harnesses import InvalidHarnesses
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType
from blizzard.hub.domain.operations.edit import BlankEditValue, ChunkEdit, filled, filled_entries
from blizzard.wire.chunk import ChunkPatchRequest


@dataclass(frozen=True)
class ChunkPatchBody:
    """A ``PATCH /chunks/{id}`` body, read field by field and applied.

    Maps a blank value (:class:`BlankEditValue`) to 422 and an unresolvable graph to 404;
    every *semantic* refusal stays ``EditService.edit``'s, so reading a body never decides
    whether the edit it asks for is allowed."""

    request: ChunkPatchRequest
    services: HubServices

    def apply(self, chunk: Chunk) -> None:
        try:
            graph_target = self._graph_target()
            migration_target, intended_migration = self._migration()
            edit = ChunkEdit(
                graph_id=graph_target.graph_id if graph_target is not None else UNSET,
                default_model=self._default_model(),
                default_effort=self._default_effort(),
                default_harnesses=self._default_harnesses(),
                intended_migration=intended_migration,
            )
        except BlankEditValue as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        try:
            self.services.edit.edit(chunk, edit, graph_target=graph_target, migration_target=migration_target)
        except InvalidHarnesses as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    def _graph_target(self) -> Graph | None:
        ref = self.request.graph_id
        if ref is None:
            return None
        graph = self.services.graphs.get(ref)
        if graph is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {ref}")
        return graph

    def _default_model(self) -> list[str] | UnsetType:
        entries = self.request.default_model
        if entries is None:
            return UNSET
        return filled_entries(entries, "default_model")

    def _default_harnesses(self) -> list[str] | UnsetType:
        entries = self.request.default_harnesses
        return UNSET if entries is None else entries

    def _default_effort(self) -> str | None | UnsetType:
        """Nullable-with-meaning: an explicit ``null`` clears the preference, an omitted
        field leaves it unchanged."""
        if "default_effort" not in self.request.model_fields_set:
            return UNSET
        value = self.request.default_effort
        return None if value is None else filled(value, "default_effort")

    def _migration(self) -> tuple[Graph | None, IntendedMigration | None | UnsetType]:
        if "intended_migration" not in self.request.model_fields_set:
            return (None, UNSET)
        patch = self.request.intended_migration
        if patch is None:
            return (None, None)
        target = graph_by_ref(self.services.graphs, filled(patch.to_graph, "to_graph"))
        node_name = filled(patch.node, "node") if patch.node is not None else None
        return (target, IntendedMigration.toward(target.graph_id, node_name))
