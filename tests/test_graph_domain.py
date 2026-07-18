"""Graph domain — ``mark_effective`` (unit tier).

The newest-``created_at``-per-``name`` rule, kept as a pure domain function
(``bzh:domain-core``) rather than re-derived at the ``GET /graphs`` edge — the
same rule :meth:`~blizzard.hub.domain.graph.IReadGraphRepository.get_enabled_by_name`
applies at lookup time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.graph import mark_effective
from tests.support import make_graph

pytestmark = pytest.mark.unit


def test_mark_effective_of_empty_list_is_empty() -> None:
    assert mark_effective([]) == {}


def test_mark_effective_marks_newest_of_one_name() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    older = make_graph("gr_old", "tiny", created_at=t0)
    newer = make_graph("gr_new", "tiny", created_at=t1)

    result = mark_effective([older, newer])

    assert result == {"gr_old": False, "gr_new": True}


def test_mark_effective_is_independent_per_name() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    a_old = make_graph("gr_a1", "a", created_at=t0)
    a_new = make_graph("gr_a2", "a", created_at=t1)
    b_only = make_graph("gr_b1", "b", created_at=t0)

    result = mark_effective([a_old, a_new, b_only])

    assert result == {"gr_a1": False, "gr_a2": True, "gr_b1": True}


def test_mark_effective_ties_on_created_at_break_by_graph_id_descending() -> None:
    """Same ``created_at``: the higher ``graph_id`` (lexically newest ULID) wins —
    the same tie order :meth:`~blizzard.hub.domain.graph.IReadGraphRepository.get_enabled_by_name`
    applies via its ``ORDER BY created_at DESC, graph_id DESC``."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    lower_id = make_graph("gr_a", "tied", created_at=t0)
    higher_id = make_graph("gr_b", "tied", created_at=t0)

    result = mark_effective([lower_id, higher_id])

    assert result == {"gr_a": False, "gr_b": True}
