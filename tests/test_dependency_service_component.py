"""``DependencyService`` over the real hub store (issue #456, component tier).

The unit tier (``tests/test_dependency_service.py``) proves the service consults nothing
about the prerequisite beyond its id, against a fake. This file drives the same verb over
a wired hub with a genuinely ``done`` prerequisite, so the acceptance case rests on the
production path rather than on the fake's silence."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from blizzard.hub.domain.dependencies import NoStandingDependencyToRelease, PrerequisiteIsEphemeral
from blizzard.hub.domain.queue import ChunkNotFound
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _resolve(hub: HubHarness, chunk_id: str):  # type: ignore[no-untyped-def]
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    return chunk


def test_an_edge_onto_a_done_prerequisite_is_accepted_with_no_satisfaction_state_written(tmp_path: Path) -> None:
    """Satisfaction is never a column: a prerequisite that has actually reached
    ``done`` is an ordinary accepted edge, and the stored row records only the
    declaration and its release pair — nothing about whether the edge is met."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    hub.services.complete.complete(_resolve(hub, prerequisite_id), by="user:alice")
    prerequisite_facts = hub.services.chunks.facts.load_facts(prerequisite_id)
    assert prerequisite_facts is not None
    assert prerequisite_facts.status().value == "done"

    edge = hub.services.dependencies.declare(
        _resolve(hub, dependent_id), _resolve(hub, prerequisite_id), by="user:alice"
    )

    assert edge.prerequisite_chunk_id == prerequisite_id
    assert edge.standing is True
    with hub.engine.connect() as conn:
        rows = conn.execute(select(s.chunk_dependencies)).mappings().all()
    assert len(rows) == 1
    assert set(rows[0]) == {
        "dependency_id",
        "dependent_chunk_id",
        "prerequisite_chunk_id",
        "declared_at",
        "declared_by",
        "released_at",
        "released_by",
    }


def test_a_second_release_of_the_same_pair_is_refused_and_the_first_release_stands(tmp_path: Path) -> None:
    """``released_at``/``released_by`` are set together, once: the standing edge is
    marked released one time, and a second release names no standing edge — the released
    row keeps the first release's stamp rather than taking a second."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    dependent = _resolve(hub, dependent_id)
    prerequisite = _resolve(hub, prerequisite_id)
    edge = hub.services.dependencies.declare(dependent, prerequisite, by="user:alice")
    released = hub.services.dependencies.release(edge, by="user:bob")

    with pytest.raises(NoStandingDependencyToRelease):
        hub.services.dependencies.release(edge, by="user:carol")

    with hub.engine.connect() as conn:
        rows = conn.execute(select(s.chunk_dependencies)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["released_by"] == "user:bob"
    assert rows[0]["released_at"] == released.released_at


def test_declaring_against_a_dependent_deleted_after_resolution_is_refused(tmp_path: Path) -> None:
    """The interleaving the shared claim lock produces: a delete lands between the
    caller's resolve and this declare's hold of the lock, so ``load_facts`` reads ``None``
    — refuse, rather than fall back to a synthetic status (issue #456)."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    dependent = _resolve(hub, dependent_id)
    prerequisite = _resolve(hub, prerequisite_id)

    hub.services.delete.delete(dependent, by="operator")

    with pytest.raises(ChunkNotFound):
        hub.services.dependencies.declare(dependent, prerequisite, by="user:alice")

    assert hub.services.chunks.dependencies.list_standing_edges() == []


def test_declaring_against_a_prerequisite_deleted_after_resolution_is_refused(tmp_path: Path) -> None:
    """The prerequisite's own half of the round-2 window: a resolved-live ``Chunk``
    goes stale when a delete lands before this declare's hold of the lock, so the
    service must re-derive its ephemerality itself, under the lock (issue #456)."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    dependent = _resolve(hub, dependent_id)
    prerequisite = _resolve(hub, prerequisite_id)

    hub.services.delete.delete(prerequisite, by="operator")

    with pytest.raises(PrerequisiteIsEphemeral) as exc_info:
        hub.services.dependencies.declare(dependent, prerequisite, by="user:alice")

    assert exc_info.value.chunk_id == prerequisite_id
    assert hub.services.chunks.dependencies.list_standing_edges() == []


def test_declaring_against_a_prerequisite_grouped_away_after_resolution_is_refused(tmp_path: Path) -> None:
    """``GroupService`` takes no claim lock at all, so this window is wider than the
    deleted-prerequisite case above, not narrower — the same re-derived read must
    still catch a resolved-live prerequisite grouped away underneath it."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    survivor_id = ingest(hub, [{"source": "default", "ref": "survivor"}], promote=False)
    dependent = _resolve(hub, dependent_id)
    prerequisite = _resolve(hub, prerequisite_id)

    hub.services.group.group(survivor_id, [prerequisite_id])

    with pytest.raises(PrerequisiteIsEphemeral) as exc_info:
        hub.services.dependencies.declare(dependent, prerequisite, by="user:alice")

    assert exc_info.value.chunk_id == prerequisite_id
    assert hub.services.chunks.dependencies.list_standing_edges() == []
