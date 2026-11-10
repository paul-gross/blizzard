"""Reading a ``PATCH /chunks/{id}`` body as the domain edit it asks for."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from blizzard.hub.api import problems_chunks
from blizzard.hub.api.graph_names import graph_by_ref
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.urgencies import stated_urgency, urgency_refusal
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.chunk.model import Chunk, IntendedMigration
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.graph.harnesses import InvalidHarnesses
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType
from blizzard.hub.domain.operations.edit import (
    BlankEditValue,
    ChunkEdit,
    EditPlan,
    filled,
    filled_entries,
)
from blizzard.hub.domain.urgency.chain import URGENCY_FIELD
from blizzard.hub.domain.urgency.model import Urgency, UrgencyRetired, UrgencyUnknown
from blizzard.wire.chunk import ChunkPatchRequest


@dataclass(frozen=True)
class ChunkPatchBody:
    """A ``PATCH /chunks/{id}`` body, read field by field and applied.

    Maps a blank value (:class:`BlankEditValue`) to 422 and an unresolvable graph to 404;
    every *semantic* refusal stays ``EditService.edit``'s, so reading a body never decides
    whether the edit it asks for is allowed."""

    request: ChunkPatchRequest
    services: TenantServices

    def apply(self, chunk: Chunk) -> EditPlan:
        """The edit applied — the plan it wrote. 422 ``urgency_unknown`` or ``urgency_retired`` for an ``urgency``
        naming no urgency or a retired one, and for an explicit ``null``."""
        try:
            graph_target = self._graph_target()
            migration_target, intended_migration = self._migration()
            edit = ChunkEdit(
                graph_id=graph_target.graph_id if graph_target is not None else UNSET,
                default_model=self._default_model(),
                default_effort=self._default_effort(),
                default_harnesses=self._default_harnesses(),
                intended_migration=intended_migration,
                urgency=self._urgency(),
            )
        except BlankEditValue as exc:
            raise ProblemHTTPException(partial(problems_chunks.blank_edit_value_problem, str(exc))) from exc
        try:
            return self.services.edit.edit(chunk, edit, graph_target=graph_target, migration_target=migration_target)
        except InvalidHarnesses as exc:
            raise ProblemHTTPException(partial(problems_chunks.invalid_harnesses_problem, str(exc))) from exc
        except (UrgencyUnknown, UrgencyRetired) as exc:  # retired after the edge's check
            raise urgency_refusal(exc) from exc

    def _urgency(self) -> Urgency | UnsetType:
        if "urgency" not in self.request.model_fields_set:
            return UNSET
        reference = self.request.urgency
        if reference is None:  # a chunk always carries an urgency, so an explicit null is no clear
            raise urgency_refusal(ConfigFieldError(URGENCY_FIELD, "must not be null"))
        urgency = stated_urgency(reference, self.services)
        assert urgency is not None, "a named reference resolves to an urgency or is refused"
        return urgency

    def _graph_target(self) -> Graph | None:
        ref = self.request.graph_id
        if ref is None:
            return None
        graph = self.services.graphs.get(ref)
        if graph is None:
            raise ProblemHTTPException(partial(problems_chunks.graph_not_found_problem, f"unknown graph {ref}"))
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
