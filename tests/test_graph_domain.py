"""Graph domain — ``mark_effective`` (unit tier).

The newest-``created_at``-per-``name`` rule, kept as a pure domain function
(``bzh:domain-core``) rather than re-derived at the ``GET /graphs`` edge — the
same rule :meth:`~blizzard.hub.domain.graph.IReadGraphRepository.get_enabled_by_name`
applies at lookup time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.graph import SessionMode, classify_session, mark_effective
from tests.support import make_graph

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# classify_session (issue #115) — the pure `session:` syntax parser.
# --------------------------------------------------------------------------- #


def test_classify_session_bare_resume_is_resume_with_no_source() -> None:
    assert classify_session("resume") == (SessionMode.RESUME, None, False)


def test_classify_session_targeted_resume_carries_the_name_as_source() -> None:
    assert classify_session("resume:build") == (SessionMode.RESUME, "build", False)


def test_classify_session_fresh_is_fresh_with_no_source() -> None:
    assert classify_session("fresh") == (SessionMode.FRESH, None, False)


@pytest.mark.parametrize("raw", ["resume:", "fresh:x", "bogus", ""])
def test_classify_session_malformed_forms_are_flagged(raw: str) -> None:
    _mode, _source, malformed = classify_session(raw)
    assert malformed is True


def test_mark_effective_of_empty_list_is_empty() -> None:
    assert mark_effective([], retired_ids=set()) == {}


def test_mark_effective_marks_newest_of_one_name() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    older = make_graph("gr_old", "tiny", created_at=t0)
    newer = make_graph("gr_new", "tiny", created_at=t1)

    result = mark_effective([older, newer], retired_ids=set())

    assert result == {"gr_old": False, "gr_new": True}


def test_mark_effective_is_independent_per_name() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    a_old = make_graph("gr_a1", "a", created_at=t0)
    a_new = make_graph("gr_a2", "a", created_at=t1)
    b_only = make_graph("gr_b1", "b", created_at=t0)

    result = mark_effective([a_old, a_new, b_only], retired_ids=set())

    assert result == {"gr_a1": False, "gr_a2": True, "gr_b1": True}


def test_mark_effective_ties_on_created_at_break_by_graph_id_descending() -> None:
    """Same ``created_at``: the higher ``graph_id`` (lexically newest ULID) wins —
    the same tie order :meth:`~blizzard.hub.domain.graph.IReadGraphRepository.get_enabled_by_name`
    applies via its ``ORDER BY created_at DESC, graph_id DESC``."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    lower_id = make_graph("gr_a", "tied", created_at=t0)
    higher_id = make_graph("gr_b", "tied", created_at=t0)

    result = mark_effective([lower_id, higher_id], retired_ids=set())

    assert result == {"gr_a": False, "gr_b": True}


# --------------------------------------------------------------------------- #
# retired_ids (issue #101) — a retired graph_id is never an effective candidate.
# --------------------------------------------------------------------------- #


def test_mark_effective_skips_a_retired_newest_and_falls_back_to_the_prior_version() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    older = make_graph("gr_old", "tiny", created_at=t0)
    newer = make_graph("gr_new", "tiny", created_at=t1)

    result = mark_effective([older, newer], retired_ids={"gr_new"})

    assert result == {"gr_old": True, "gr_new": False}


def test_mark_effective_with_every_version_of_a_name_retired_marks_none_effective() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    older = make_graph("gr_old", "tiny", created_at=t0)
    newer = make_graph("gr_new", "tiny", created_at=t1)

    result = mark_effective([older, newer], retired_ids={"gr_old", "gr_new"})

    assert result == {"gr_old": False, "gr_new": False}


def test_mark_effective_retired_ids_is_independent_per_name() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    a = make_graph("gr_a", "a", created_at=t0)
    b = make_graph("gr_b", "b", created_at=t0)

    result = mark_effective([a, b], retired_ids={"gr_a"})

    assert result == {"gr_a": False, "gr_b": True}


def test_mark_effective_requires_retired_ids_explicitly() -> None:
    """``retired_ids`` carries no default (issue #101 lockstep note): a caller that
    forgets it gets a ``TypeError``, never a silent fall-back to the pre-#101
    every-graph-is-a-candidate behavior."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    only = make_graph("gr_only", "solo", created_at=t0)

    with pytest.raises(TypeError):
        mark_effective([only])  # type: ignore[call-arg]
