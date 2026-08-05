"""Graph retire / re-enable — an operator's reversible brake over a ``graph_id`` (#101).

Retiring appends a ``graph.retired`` fact and re-enabling a ``graph.enabled`` one;
newest-fact-wins, so either direction needs no extra bookkeeping. The ``graphs`` row and
its ``definition_yaml`` are never touched — this is append-only, not a mutation.
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

    def set_follow_latest(self, graph: Graph, *, follow_latest: bool | None, by: str) -> None:
        """Append this graph's follow-latest policy — the tri-state (issue #164).

        ``None`` reverts to inheriting the configured default; clearing an override is an
        appended fact like any other (pinned by tests/test_follow_latest_policy.py).
        """
        self._graphs.record_policy(graph.graph_id, follow_latest=follow_latest, at=self._clock.now(), by=by)
