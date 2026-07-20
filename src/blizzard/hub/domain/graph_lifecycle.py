"""Graph retire / re-enable — an operator's reversible brake over one specific
``graph_id`` (issue #101).

``blizzard hub graph retire <graph_id>`` appends a ``graph.retired`` fact;
``blizzard hub graph enable <graph_id>`` appends ``graph.enabled``. Newest-fact-wins
(:meth:`~blizzard.hub.domain.graph.IReadGraphRepository.is_retired`), so a re-enable
after a retire derives ``retired=False`` again with no extra bookkeeping — structurally
mirrors :class:`~blizzard.hub.domain.pause.PauseService`, minus the refusal: retiring or
re-enabling a graph is never blocked by anything a chunk is doing (the out-of-scope note
in issue #101 is deliberate — existing pins on a retired graph are left to run out, only
*new* usage is blocked, and that block lives at the graph-selection edges themselves:
``GraphStore.get_enabled_by_name`` and ``POST /chunks/{id}/graph``).

The ``graphs`` row and its ``definition_yaml`` are never touched here — retiring is an
append-only fact, not a mutation of the immutable graph itself.

Holds the *write* graph repository (``bzh:controller-read-only``); the route resolves
the graph (``bzh:domain-takes-objects``) and delegates here.

Publishes no event: graphs carry no SSE channel at all (the broker's own
``publish_*`` methods are chunk/queue/runner-scoped only), so retire/enable are no
different from every other graph write in that respect — a second board tab only
sees the flip on its next full re-fetch, not live.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.graph import Graph, IWriteGraphRepository


class GraphLifecycleService:
    """Set or clear a graph's retired brake without touching its immutable row (issue #101)."""

    def __init__(self, *, graphs: IWriteGraphRepository, clock: IClock) -> None:
        self._graphs = graphs
        self._clock = clock

    def retire(self, graph: Graph, *, by: str) -> None:
        """Append ``graph.retired`` — excludes ``graph.graph_id`` from name resolution.

        Idempotent: retiring an already-retired graph just appends another
        ``retired=True`` fact, a harmless no-op via newest-fact-wins.
        """
        self._graphs.record_lifecycle(graph.graph_id, retired=True, at=self._clock.now(), by=by)

    def enable(self, graph: Graph, *, by: str) -> None:
        """Append ``graph.enabled`` — restores normal newest-per-name derivation.

        Idempotent: enabling an already-enabled graph (or one with no lifecycle fact at
        all) just appends another ``retired=False`` fact, a harmless no-op.
        """
        self._graphs.record_lifecycle(graph.graph_id, retired=False, at=self._clock.now(), by=by)
