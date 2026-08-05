"""The single seam every mutating chunk route publishes a ``chunk-changed`` frame through (issue #212),
so every emit site enriches the frame the same way.

``snapshot_chunk_status`` is the pre-mutation read: ``bzh:facts-not-status`` means the only truthful
"before" is a derivation over the facts as they stood before the write, handed back to
:func:`publish_chunk_changed` as ``prev_status``."""

from __future__ import annotations

from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import ChunkFacts, derive_chunk_status, describe_chunk_change, newest_transition
from blizzard.hub.events.broker import ChunkChangeCause


def snapshot_chunk_status(services: HubServices, chunk_id: str) -> str | None:
    """The chunk's status right now, before the mutation. ``None`` when the chunk does not yet exist."""
    facts = services.chunks.load_facts(chunk_id)
    if facts is None:
        return None
    return derive_chunk_status(facts).value


def publish_chunk_changed(
    services: HubServices,
    chunk_id: str,
    *,
    cause: ChunkChangeCause | None,
    prev_status: str | None,
    status: str | None = None,
    key: str | None = None,
) -> None:
    """Publish a fully enriched ``chunk-changed`` frame for ``chunk_id``, loading the post-mutation
    facts, chunk, and pinned graph itself. Status is derived from those facts unless ``status``
    overrides it. ``key`` (issue #213) identifies the durable fact just written — e.g.
    ``f"transitions:{transition_id}"``, matching :class:`~blizzard.hub.domain.work.ActivityRow`'s key
    format — or ``None``. Degrades to a bare ``{chunk_id, status}`` frame rather than raising."""
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    resolved_status = status if status is not None else derive_chunk_status(facts).value
    chunk = services.chunks.get(chunk_id)
    graph = services.graphs.get(chunk.graph_id) if chunk is not None else None
    if chunk is None or graph is None:
        services.events.publish_chunk_changed(chunk_id, resolved_status, key=key)
        return

    from_graph = None
    transition = newest_transition(facts)
    if transition is not None and transition.graph_id is not None and transition.graph_id != graph.graph_id:
        from_graph = services.graphs.get(transition.graph_id)

    route = services.chunks.route_of(chunk_id)
    runner_id = route.runner_id if route is not None else None

    change = describe_chunk_change(
        chunk, graph, facts, prev_status=prev_status, runner_id=runner_id, cause=cause, from_graph=from_graph
    )
    services.events.publish_chunk_changed(
        chunk_id,
        resolved_status,
        prev_status=change.prev_status,
        prev_node=change.prev_node,
        node=change.node,
        runner_id=change.runner_id,
        cause=cause,  # change.cause is a widened `str | None` (`bzh:domain-core` — the
        # domain stays events-layer-free); this module already holds the typed value.
        graph_id=change.graph_id,
        key=key,
    )
