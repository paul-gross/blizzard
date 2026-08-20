"""Reading a ``PATCH /chunks/{id}`` body as the domain edit it asks for."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status

from blizzard.hub.api.graph_names import graph_by_ref
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.edit import UNSET, ChunkEdit, UnsetType
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import Chunk, IntendedMigration, MigrationMode
from blizzard.wire.chunk import ChunkPatchRequest


@dataclass(frozen=True)
class ChunkPatchBody:
    """A ``PATCH /chunks/{id}`` body, read field by field and applied (issue #124).

    Refuses a blank value with 422 and an unresolvable graph with 404; every *semantic*
    refusal stays ``EditService.edit``'s, so reading a body never decides whether the edit
    it asks for is allowed."""

    request: ChunkPatchRequest
    services: HubServices

    def apply(self, chunk: Chunk) -> None:
        graph_target = self._graph_target()
        migration_target, intended_migration = self._migration()
        edit = ChunkEdit(
            graph_id=graph_target.graph_id if graph_target is not None else UNSET,
            default_model=self._default_model(),
            default_effort=self._default_effort(),
            intended_migration=intended_migration,
        )
        self.services.edit.edit(chunk, edit, graph_target=graph_target, migration_target=migration_target)

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
        stripped = [entry.strip() for entry in entries]
        if any(not entry for entry in stripped):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="default_model entries must not be blank"
            )
        return stripped

    def _default_effort(self) -> str | None | UnsetType:
        """Nullable-with-meaning: an explicit ``null`` clears the preference, an omitted
        field leaves it unchanged."""
        if "default_effort" not in self.request.model_fields_set:
            return UNSET
        value = self.request.default_effort
        return None if value is None else self._stripped(value, "default_effort")

    def _migration(self) -> tuple[Graph | None, IntendedMigration | None | UnsetType]:
        if "intended_migration" not in self.request.model_fields_set:
            return (None, UNSET)
        patch = self.request.intended_migration
        if patch is None:
            return (None, None)
        target = graph_by_ref(self.services.graphs, self._stripped(patch.to_graph, "to_graph"))
        node_name = self._stripped(patch.node, "node") if patch.node is not None else None
        mode = MigrationMode.FORCED if node_name is not None else MigrationMode.AUTO
        return (target, IntendedMigration(mode=mode, graph_id=target.graph_id, node_name=node_name))

    def _stripped(self, value: str, field_name: str) -> str:
        text = value.strip()
        if not text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} must not be blank"
            )
        return text
