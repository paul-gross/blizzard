"""The delete/group race is atomic (component tier).

``DeleteService`` and ``GroupService`` share the residual fleet-wide ``threading.Lock``
(``bzh:store-exclusive-write``): deleting a chunk releases its own outgoing edges, which
races a concurrent fold reminting one of those same edges onto a chunk neither
transaction's row lock names — the fold locks only the survivor and its merge ids, never
the folded prerequisite's *dependent*. On Postgres, the row lock alone cannot serialize
that pair; only the shared lock does. This test tier cannot isolate that contribution on
its own: SQLite admits one writer transaction at a time regardless of which rows it
locks, so the delete blocks here even with the shared lock stubbed to a no-op — a probe
this test alone cannot distinguish from proof. What it does prove, tier-independent: the
shared lock is acquired before the fold's edge rewrite, and no standing edge survives
naming a chunk the delete just removed — the outcome an operator would see under either
backend."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.chunk.ports.dependencies import IWriteChunkDependenciesRepository
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _writable_dependencies(hub: HubHarness) -> IWriteChunkDependenciesRepository:
    return cast(IWriteChunkDependenciesRepository, hub.services.chunks.dependencies)


def test_a_delete_blocks_while_a_fold_holds_the_shared_lock_mid_rewrite(tmp_path: Path) -> None:
    """``dependent`` depends on ``prerequisite``; folding ``prerequisite`` into
    ``survivor`` remints that edge onto ``survivor``, naming ``dependent`` — a chunk the
    fold's own row lock never names. Deleting ``dependent`` concurrently must block on the
    shared lock until the fold commits, then see the freshly-minted edge and release it,
    rather than deleting the chunk while the fold is still mid-rewrite."""
    hub = build_hub(tmp_path)
    survivor_id = ingest(hub, [{"source": "default", "ref": "survivor"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prerequisite"}], promote=False)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)

    dependent = hub.services.chunks.record.get(dependent_id)
    prerequisite = hub.services.chunks.record.get(prerequisite_id)
    assert dependent is not None
    assert prerequisite is not None
    hub.services.dependencies.declare(dependent, prerequisite, by="user:alice")

    entered_write = threading.Event()
    release_write = threading.Event()
    dependencies = _writable_dependencies(hub)
    real_record_fold_locked = dependencies.record_fold_locked

    def _blocking_record_fold_locked(handle, targets, *, grouped_into, by, at):  # type: ignore[no-untyped-def]
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the fold's write"
        return real_record_fold_locked(handle, targets, grouped_into=grouped_into, by=by, at=at)

    dependencies.record_fold_locked = _blocking_record_fold_locked  # type: ignore[method-assign]

    group_result: dict[str, object] = {}

    def _group() -> None:
        group_result["result"] = hub.services.group.group(survivor_id, [prerequisite_id])

    group_thread = threading.Thread(target=_group)
    group_thread.start()
    assert entered_write.wait(timeout=5), "the fold never reached its (patched) write"

    delete_result: dict[str, object] = {}

    def _delete() -> None:
        chunk = hub.services.chunks.record.get(dependent_id)
        assert chunk is not None
        delete_result["id"] = hub.services.delete.delete(chunk, by="operator")

    delete_thread = threading.Thread(target=_delete)
    delete_thread.start()
    delete_thread.join(timeout=0.3)
    assert delete_thread.is_alive(), "the delete completed while the fold still held the shared lock — not atomic"

    release_write.set()
    group_thread.join(timeout=5)
    delete_thread.join(timeout=5)

    assert "result" in group_result, group_result
    assert "id" in delete_result, delete_result

    assert hub.client.get(f"/api/chunks/{dependent_id}").status_code == 404
    assert hub.client.get(f"/api/chunks/{prerequisite_id}").status_code == 404
    survivor = hub.client.get(f"/api/chunks/{survivor_id}").json()
    # The fold minted `dependent -> survivor`; the delete that resumes right after sees it
    # fresh and releases it — no standing edge is left naming the now-deleted `dependent`.
    assert survivor["neighborhood"]["dependents"] == []
