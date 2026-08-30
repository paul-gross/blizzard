"""Graph domain — mint selection (unit tier).

The newest-``created_at``-per-``name`` rule, kept in the domain (``bzh:domain-core``)
rather than re-derived at the ``GET /graphs`` edge.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.node_steps import SessionMode
from blizzard.hub.domain.graph import Mint, Mints, SessionRef
from tests.support import make_graph

pytestmark = pytest.mark.unit


# SessionRef (issue #115) — the pure `session:` syntax parser.


def test_session_ref_bare_resume_is_resume_with_no_source() -> None:
    assert SessionRef.of("resume") == SessionRef(SessionMode.RESUME)


def test_session_ref_targeted_resume_carries_the_name_as_source() -> None:
    assert SessionRef.of("resume:build") == SessionRef(SessionMode.RESUME, "build")


def test_session_ref_fresh_is_fresh_with_no_source() -> None:
    assert SessionRef.of("fresh") == SessionRef(SessionMode.FRESH)


def test_session_ref_named_fresh_carries_the_name_as_source() -> None:
    # #144's new form; no already-minted graph can carry it, so adding it is back-compatible.
    assert SessionRef.of("fresh:code") == SessionRef(SessionMode.FRESH, "code")


@pytest.mark.parametrize("raw", ["resume:", "fresh:", "bogus", ""])
def test_session_ref_malformed_forms_are_flagged(raw: str) -> None:
    assert SessionRef.of(raw).malformed is True


def test_mints_effective_of_empty_list_is_empty() -> None:
    assert Mints.of([], retired_ids=set()).effective == {}


def test_mints_effective_marks_newest_of_one_name() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    older = make_graph("gr_old", "tiny", created_at=t0)
    newer = make_graph("gr_new", "tiny", created_at=t1)

    result = Mints.of([older, newer], retired_ids=set()).effective

    assert result == {"gr_old": False, "gr_new": True}


def test_mints_effective_is_independent_per_name() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    a_old = make_graph("gr_a1", "a", created_at=t0)
    a_new = make_graph("gr_a2", "a", created_at=t1)
    b_only = make_graph("gr_b1", "b", created_at=t0)

    result = Mints.of([a_old, a_new, b_only], retired_ids=set()).effective

    assert result == {"gr_a1": False, "gr_a2": True, "gr_b1": True}


def test_mints_effective_ties_on_created_at_break_by_graph_id_descending() -> None:
    """Same ``created_at``: the higher ``graph_id`` (lexically newest ULID) wins —
    the same tie order :meth:`~blizzard.hub.domain.graph.IReadGraphRepository.get_enabled_by_name`
    applies via its ``ORDER BY created_at DESC, graph_id DESC``."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    lower_id = make_graph("gr_a", "tied", created_at=t0)
    higher_id = make_graph("gr_b", "tied", created_at=t0)

    result = Mints.of([lower_id, higher_id], retired_ids=set()).effective

    assert result == {"gr_a": False, "gr_b": True}


# retired_ids (issue #101) — a retired graph_id is never an effective candidate.


def test_mints_effective_skips_a_retired_newest_and_falls_back_to_the_prior_version() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    older = make_graph("gr_old", "tiny", created_at=t0)
    newer = make_graph("gr_new", "tiny", created_at=t1)

    result = Mints.of([older, newer], retired_ids={"gr_new"}).effective

    assert result == {"gr_old": True, "gr_new": False}


def test_mints_effective_with_every_version_of_a_name_retired_marks_none_effective() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    older = make_graph("gr_old", "tiny", created_at=t0)
    newer = make_graph("gr_new", "tiny", created_at=t1)

    result = Mints.of([older, newer], retired_ids={"gr_old", "gr_new"}).effective

    assert result == {"gr_old": False, "gr_new": False}


def test_mints_effective_retired_ids_is_independent_per_name() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    a = make_graph("gr_a", "a", created_at=t0)
    b = make_graph("gr_b", "b", created_at=t0)

    result = Mints.of([a, b], retired_ids={"gr_a"}).effective

    assert result == {"gr_a": False, "gr_b": True}


def test_mints_effective_requires_retired_ids_explicitly() -> None:
    """``retired_ids`` carries no default (issue #101 lockstep note): a caller that
    forgets it gets a ``TypeError``, never a silent fall-back to the pre-#101
    every-graph-is-a-candidate behavior."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    only = make_graph("gr_only", "solo", created_at=t0)

    with pytest.raises(TypeError):
        Mints.of([only])  # type: ignore[call-arg]


# Mint.newer_than — the newest-wins order, named rather than open-coded (issue #164)

_T0 = datetime(2026, 5, 1, tzinfo=UTC)


@pytest.mark.unit
def test_newer_than_orders_by_created_at() -> None:
    older = make_graph("gr_b", "wf", created_at=_T0)
    newer = make_graph("gr_a", "wf", created_at=_T0 + timedelta(minutes=1))
    # `created_at` dominates the id: the newer mint wins even with the smaller id.
    assert Mint.of(newer).newer_than(Mint.of(older)) is True
    assert Mint.of(older).newer_than(Mint.of(newer)) is False


@pytest.mark.unit
def test_newer_than_breaks_a_created_at_tie_on_graph_id() -> None:
    """Two mints can share a `created_at` — a fixed clock, or two mints inside one tick.
    ULIDs sort lexically by creation, so the id is the deterministic tiebreak, kept in
    lockstep with `Mints.effective` and `get_enabled_by_name`'s own ORDER BY."""
    low = make_graph("gr_aaa", "wf", created_at=_T0)
    high = make_graph("gr_bbb", "wf", created_at=_T0)
    assert Mint.of(high).newer_than(Mint.of(low)) is True
    assert Mint.of(low).newer_than(Mint.of(high)) is False


@pytest.mark.unit
def test_a_mint_is_not_newer_than_itself() -> None:
    """Strictness is what the follow-latest policy leans on — an equal mint is a no-op,
    not a migration onto the graph the chunk is already pinned to."""
    graph = make_graph("gr_a", "wf", created_at=_T0)
    assert Mint.of(graph).newer_than(Mint.of(graph)) is False
