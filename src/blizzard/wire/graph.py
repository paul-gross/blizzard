"""Graph mint request and views.

``POST /graphs`` takes a YAML definition, validates it (errors reject, warnings
flag), inlines every file reference, and mints an immutable graph.
The request carries the YAML text; the response is a :class:`GraphView`. An invalid
definition returns **422** with a :class:`GraphValidationReport`.
"""

from __future__ import annotations

from pydantic import BaseModel


class GraphMintRequest(BaseModel):
    """A graph definition to mint — the raw YAML body."""

    definition_yaml: str


class GraphValidationReport(BaseModel):
    """The validator's verdict — the 422 body when errors reject a mint."""

    ok: bool
    errors: list[str] = []
    warnings: list[str] = []


class GraphNodeView(BaseModel):
    """A reified node in a minted graph."""

    node_id: str
    name: str
    executor: str
    judged_by: str


class GraphView(BaseModel):
    """A minted graph as served by ``GET /graphs`` and the mint response."""

    graph_id: str
    name: str
    entry_node_id: str
    enabled: bool
    nodes: list[GraphNodeView] = []
    warnings: list[str] = []
