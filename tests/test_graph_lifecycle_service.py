"""GraphLifecycleService (unit tier) — the operator's retire/re-enable brake over one
specific ``graph_id``, facts only (issue #101).

A fake stands in for the store — only ``record_lifecycle`` is meaningfully
implemented; every other seam is unreachable from :meth:`GraphLifecycleService.retire`/
``enable`` and raises loudly if a regression starts calling it (``bzh:domain-core`` — no
store, no tokens). Copies :mod:`tests.test_pause_service`'s fake-repo pattern exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph import Graph, IWriteGraphRepository
from blizzard.hub.domain.graph_lifecycle import GraphLifecycleService

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_GRAPH = Graph(graph_id="gr_1", name="alpha", entry_node_id="nd_1", nodes=[], edges=[], created_at=_T0)


@dataclass
class _FakeGraphRepo:
    """Only ``record_lifecycle`` is live; anything else is a bug."""

    recorded: list[tuple[str, bool, str, datetime]] = field(default_factory=list)

    def record_lifecycle(self, graph_id: str, *, retired: bool, at: datetime, by: str) -> None:
        self.recorded.append((graph_id, retired, by, at))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"GraphLifecycleService should not touch {name!r}")


def _as_write_repo(repo: _FakeGraphRepo) -> IWriteGraphRepository:
    """Assert the fake satisfies the Protocol GraphLifecycleService depends on (see module docstring)."""
    return cast(IWriteGraphRepository, repo)


def test_retire_records_a_retired_true_fact() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeGraphRepo()
    service = GraphLifecycleService(graphs=_as_write_repo(repo), clock=clock)

    service.retire(_GRAPH, by="operator")

    assert repo.recorded == [("gr_1", True, "operator", _T0)]


def test_enable_records_a_retired_false_fact() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeGraphRepo()
    service = GraphLifecycleService(graphs=_as_write_repo(repo), clock=clock)

    service.enable(_GRAPH, by="operator")

    assert repo.recorded == [("gr_1", False, "operator", _T0)]


def test_retire_twice_is_a_harmless_no_op() -> None:
    """Idempotent by repetition: the second retire is just another newest-wins fact."""
    clock = FixedClock(instant=_T0)
    repo = _FakeGraphRepo()
    service = GraphLifecycleService(graphs=_as_write_repo(repo), clock=clock)

    service.retire(_GRAPH, by="operator")
    service.retire(_GRAPH, by="operator")

    assert repo.recorded == [("gr_1", True, "operator", _T0), ("gr_1", True, "operator", _T0)]


def test_enable_is_idempotent_on_a_never_retired_graph() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeGraphRepo()
    service = GraphLifecycleService(graphs=_as_write_repo(repo), clock=clock)

    service.enable(_GRAPH, by="operator")

    assert repo.recorded == [("gr_1", False, "operator", _T0)]


def test_set_by_is_carried_onto_the_recorded_fact() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeGraphRepo()
    service = GraphLifecycleService(graphs=_as_write_repo(repo), clock=clock)

    service.retire(_GRAPH, by="paul")

    assert repo.recorded == [("gr_1", True, "paul", _T0)]


def test_retire_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    repo = _FakeGraphRepo()
    service = GraphLifecycleService(graphs=_as_write_repo(repo), clock=clock)

    service.retire(_GRAPH, by="operator")

    assert repo.recorded == [("gr_1", True, "operator", later)]
