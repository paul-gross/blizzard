"""Read retirement holdings on the transaction holding the registry decision lock."""

from collections.abc import Sequence

from sqlalchemy import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.execution.detach import held_routes
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_rows import routes_of_conn
from blizzard.hub.store.scoped import ScopedConnections


def retirement_holdings(
    store: ScopedConnections, conn: Connection, runner_ids: Sequence[str]
) -> dict[str, list[Route]]:
    """Resolve current routes and their status without taking chunk locks under the registry lock."""
    chunk_ids: set[str] = set()
    for batch in id_batches(list(runner_ids)):
        chunk_ids.update(
            conn.execute(
                store.select(s.route_created, s.route_created.c.chunk_id)
                .where(s.route_created.c.runner_id.in_(batch))
                .distinct()
            ).scalars()
        )
    routes: list[Route] = []
    for batch in id_batches(sorted(chunk_ids)):
        routes.extend(routes_of_conn(conn, batch).values())
    wanted = set(runner_ids)
    routes = [route for route in routes if route.runner_id in wanted]
    facts = ChunkFactsStore(store).status_facts_for_conn(conn, [r.chunk_id for r in routes])
    held = held_routes(routes, {chunk_id: fact.status() for chunk_id, fact in facts.items()})
    result: dict[str, list[Route]] = {runner_id: [] for runner_id in runner_ids}
    for route in sorted(held, key=lambda item: item.chunk_id):
        result[route.runner_id].append(route)
    return result
