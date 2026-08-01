"""The delivery closure fact (issue #216) — the durable store seam Phase 4's
:class:`~blizzard.hub.domain.work_closure.DeliveryClosureReconciler` reads and writes.

``ChunkStore.closable_work_refs()``/``record_work_item_closure()`` are exercised
against a real, migrated store (component tier) — the same choice
``tests/test_forge_status.py`` makes for ``live_work_refs()``, its own structural
twin. The landing gate is ``has_landed_repos`` alone, not chunk status: a chunk that
landed and was *later* stopped still owes a closure attempt, while one that never
landed never does, whether or not it is stopped (the plan's own recorded deviation
from an inline deliver-step close).
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.work import ClosableWorkRef, IWriteChunkRepository, WorkItemCloseOutcome, WorkRef
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _writable(hub: HubHarness) -> IWriteChunkRepository:
    """A test-only cast, mirroring ``tests/test_hub_command_node.py``'s own helper:
    ``HubHarness.services.chunks`` is read-typed, but the live object is always the
    write-capable ``ChunkStore``."""
    return cast(IWriteChunkRepository, hub.services.chunks)


def _land(hub: HubHarness, chunk_id: str, *, repo: str = "widget") -> None:
    """Simulate a generic hub command node's mid-run ``merged/<repo>`` marker —
    the current landing truth :func:`~blizzard.hub.domain.work.has_landed_repos` reads
    (issue #67), independent of any real graph/node machinery."""
    _writable(hub).record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name=f"merged/{repo}",
        content="sha",
        at=hub.clock.now(),
    )


# --------------------------------------------------------------------------- #
# IReadChunkRepository.closable_work_refs() — real ChunkStore, real migrations
# --------------------------------------------------------------------------- #


def test_closable_work_refs_includes_a_landed_chunks_refs(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=WorkRef(source="default", ref="1")) in refs


def test_closable_work_refs_excludes_an_unlanded_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    assert hub.services.chunks.closable_work_refs() == []


def test_closable_work_refs_excludes_a_stopped_chunk_that_never_landed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="test")

    assert hub.services.chunks.closable_work_refs() == []


def test_closable_work_refs_includes_a_landed_chunk_later_stopped(tmp_path: Path) -> None:
    """The plan's own recorded deviation: ``has_landed_repos`` is the sole gate, not
    chunk status — a chunk that landed and was *then* stopped still owes a closure
    attempt, since it was in fact delivered."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    chunk = hub.services.chunks.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="test")

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=WorkRef(source="default", ref="1")) in refs


def test_closable_work_refs_excludes_a_grouped_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    survivor_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    merged_id = ingest(hub, [{"source": "default", "ref": "2"}], promote=False)
    _land(hub, merged_id)

    hub.services.group.group(survivor_id, [merged_id])

    refs = hub.services.chunks.closable_work_refs()
    assert WorkRef(source="default", ref="2") not in {r.ref for r in refs}


def test_closable_work_refs_excludes_a_ref_with_a_closed_fact(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) not in refs


def test_closable_work_refs_excludes_a_ref_with_a_gone_fact(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.GONE, reason="deleted", at=hub.clock.now()
    )

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) not in refs


def test_closable_work_refs_still_includes_a_ref_with_only_a_failed_fact(tmp_path: Path) -> None:
    """``failed`` is not terminal — the reconciler retries it on the next sweep."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.FAILED, reason="boom", at=hub.clock.now()
    )

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) in refs


# --------------------------------------------------------------------------- #
# IWriteChunkRepository.record_work_item_closure() — the idempotent-bool contract
# --------------------------------------------------------------------------- #


def test_record_work_item_closure_returns_true_on_the_first_write(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    wrote = _writable(hub).record_work_item_closure(
        chunk_id,
        pointer=WorkRef(source="default", ref="1"),
        outcome=WorkItemCloseOutcome.CLOSED,
        reason=None,
        at=hub.clock.now(),
    )

    assert wrote is True


def test_record_work_item_closure_is_idempotent_per_chunk_source_ref_outcome(tmp_path: Path) -> None:
    """Driven twice, then re-read: the second write is a no-op and returns False —
    the mutation-review re-read (``bzh:mutation-review-selection``)."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    pointer = WorkRef(source="default", ref="1")

    first = _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )
    second = _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )

    assert first is True
    assert second is False
    _land(hub, chunk_id)
    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) not in hub.services.chunks.closable_work_refs()


def test_record_work_item_closure_allows_a_distinct_outcome_for_the_same_ref(tmp_path: Path) -> None:
    """A ``failed`` attempt followed by a later ``closed`` one is two distinct rows —
    the unique key is ``(chunk_id, source, ref, outcome)``, not ``(chunk_id, source, ref)``."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    pointer = WorkRef(source="default", ref="1")

    failed = _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.FAILED, reason="boom", at=hub.clock.now()
    )
    closed = _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )

    assert failed is True
    assert closed is True
