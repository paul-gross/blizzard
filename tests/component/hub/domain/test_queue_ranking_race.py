"""A ranking decision lands on the list its own write read (component tier).

Every move, whole-order replace, and list-entering promote or demote decides from the list's
current positions. A competing ranking write that commits between that read and the write would
leave the later write stamping a slot computed against an order that no longer exists. Each
test here holds the first request at its first position write, starts the competitor on a
second thread, and waits a bounded time for it to commit: a decision whose read and write
share one locked unit holds the competitor behind it, so the two commit as some serial order.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.queue import BACKLOG_RANKING_LOCK
from blizzard.hub.domain.operations.promote import PromoteService
from blizzard.hub.store.internal.chunk_queue_store import ChunkQueueStore
from tests.support.scaffolding import HubHarness, build_hub, chunk_stores, pointer_token, ranking_unit

#: How long the held request waits for its competitor to commit before writing anyway.
_COMPETITOR_WINDOW_SECONDS = 1.0


def _ingest_backlog(hub: HubHarness, n: int) -> str:
    pointer = {"source": "default", "ref": str(n)}
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]})
    assert resp.status_code == 201, resp.text
    hub.clock.advance(timedelta(seconds=1))
    return str(resp.json()["chunk_id"])


def _ingest_ready(hub: HubHarness, n: int) -> str:
    chunk_id = _ingest_backlog(hub, n)
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def _order(hub: HubHarness, path: str, names: dict[str, str]) -> list[str]:
    """``path``'s list in order, each chunk read as its name in ``names`` (name to chunk id)."""
    resp = hub.client.get(path)
    assert resp.status_code == 200, resp.text
    by_id = {chunk_id: name for name, chunk_id in names.items()}
    return [by_id[entry["chunk_id"]] for entry in resp.json()["entries"]]


def _ready_order(hub: HubHarness, names: dict[str, str]) -> list[str]:
    return _order(hub, "/api/queue", names)


def _backlog_order(hub: HubHarness, names: dict[str, str]) -> list[str]:
    return _order(hub, "/api/backlog", names)


def _move(hub: HubHarness, path: str, chunk_id: str, after_chunk_id: str | None) -> int:
    return hub.client.post(path, json={"chunk_id": chunk_id, "after_chunk_id": after_chunk_id}).status_code


class _Competitor:
    """Holds the first request at its first call to the queue store's position ``write`` and runs
    ``request`` on a second thread meanwhile, waiting up to :data:`_COMPETITOR_WINDOW_SECONDS` for it
    to commit."""

    def __init__(self, request: Callable[[], int]) -> None:
        self._request = request
        self._fired = False
        self.status: int | None = None
        self.committed_inside_window = False
        self.thread = threading.Thread(target=self._run)

    def _run(self) -> None:
        self.status = self._request()

    def hold(self, monkeypatch: pytest.MonkeyPatch, write: str = "record_queue_positions") -> None:
        original = getattr(ChunkQueueStore, write)

        def held(store: ChunkQueueStore, *args: Any, **kwargs: Any) -> None:
            if not self._fired:
                self._fired = True
                self.thread.start()
                self.thread.join(timeout=_COMPETITOR_WINDOW_SECONDS)
                self.committed_inside_window = not self.thread.is_alive()
            original(store, *args, **kwargs)

        monkeypatch.setattr(ChunkQueueStore, write, held)

    def finish(self) -> int:
        assert self._fired, "the held request never reached its position write"
        self.thread.join(timeout=10)
        assert not self.thread.is_alive(), "the competitor never finished"
        assert self.status is not None
        return self.status


def test_two_moves_in_one_list_commit_as_some_serial_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Over ``[a, b, c]``, "move c after a" and "move a after c" each read the list, then write.
    Run serially they give ``[c, a, b]`` or ``[b, a, c]``; a move landing a slot computed before
    the other committed gives ``[c, b, a]``, undoing the first move's request."""
    hub = build_hub(tmp_path)
    a, b, c = _ingest_ready(hub, 1), _ingest_ready(hub, 2), _ingest_ready(hub, 3)
    assert hub.client.put("/api/queue", json={"chunk_ids": [a, b, c]}).status_code == 200

    competitor = _Competitor(
        lambda: hub.client.post("/api/queue/position", json={"chunk_id": a, "after_chunk_id": c}).status_code
    )
    competitor.hold(monkeypatch)
    first = hub.client.post("/api/queue/position", json={"chunk_id": c, "after_chunk_id": a})

    assert first.status_code == 200, first.text
    assert competitor.finish() == 200
    assert _ready_order(hub, {"a": a, "b": b, "c": c}) in (["c", "a", "b"], ["b", "a", "c"])
    assert not competitor.committed_inside_window, "the competing move committed inside the first move's unit"


def test_a_promote_after_an_anchor_lands_as_some_serial_order_against_a_move_of_the_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[a, b, c]`` with no representable slot left between ``a`` and ``b``: promoting ``n`` after
    ``a`` renormalizes the list and lands ``n`` right after ``a``, while "move a after c"
    concurrently sends ``a`` to the tail. Run serially they give ``[n, b, c, a]`` or
    ``[b, c, a, n]``; a renormalization computed before the move committed puts ``a`` back at the
    head, giving ``[a, n, b, c]`` — the move silently undone."""
    hub = build_hub(tmp_path)
    a, b, c = _ingest_ready(hub, 1), _ingest_ready(hub, 2), _ingest_ready(hub, 3)
    n = _ingest_backlog(hub, 4)
    stores = chunk_stores(hub.engine, hub.clock)
    with ranking_unit(hub.engine) as unit:
        stores.queue.record_queue_positions(
            [(a, 1.0), (b, math.nextafter(1.0, math.inf)), (c, 2.0)], at=hub.clock.now(), transaction=unit
        )
    names = {"a": a, "b": b, "c": c, "n": n}
    assert _ready_order(hub, names) == ["a", "b", "c"]

    competitor = _Competitor(
        lambda: hub.client.post("/api/queue/position", json={"chunk_id": a, "after_chunk_id": c}).status_code
    )
    competitor.hold(monkeypatch)
    promoted = hub.client.post(f"/api/chunks/{n}/promote", json={"after_chunk_id": a})

    assert promoted.status_code == 202, promoted.text
    assert competitor.finish() == 200
    assert _ready_order(hub, names) in (["n", "b", "c", "a"], ["b", "c", "a", "n"])
    assert not competitor.committed_inside_window, "the competing move committed inside the promote's unit"


def test_a_promote_after_an_anchor_moved_just_before_its_unit_lands_right_after_the_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over ``[a, b, c]``, "move a after c" commits after the promote's caller resolved its chunk and
    before the promote's unit opens: ``n`` lands right after ``a`` where ``a`` now stands."""
    hub = build_hub(tmp_path)
    a, b, c = _ingest_ready(hub, 1), _ingest_ready(hub, 2), _ingest_ready(hub, 3)
    n = _ingest_backlog(hub, 4)
    assert hub.client.put("/api/queue", json={"chunk_ids": [a, b, c]}).status_code == 200
    original = PromoteService.promote

    def after_the_anchor_moved(service: PromoteService, chunk: Chunk, **kwargs: Any) -> int | None:
        assert _move(hub, "/api/queue/position", a, c) == 200
        return original(service, chunk, **kwargs)

    monkeypatch.setattr(PromoteService, "promote", after_the_anchor_moved)
    promoted = hub.client.post(f"/api/chunks/{n}/promote", json={"after_chunk_id": a})

    assert promoted.status_code == 202, promoted.text
    assert _ready_order(hub, {"a": a, "b": b, "c": c, "n": n}) == ["b", "c", "a", "n"]


def test_a_demote_after_an_anchor_lands_as_some_serial_order_against_a_move_of_the_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backlog mirror of the promote race: backlog ``[p, q, r]`` with no representable slot left
    between ``p`` and ``q``, demoting ``d`` after ``p`` while "move p after r" runs. Run serially they
    give ``[d, q, r, p]`` or ``[q, r, p, d]``; a renormalization computed before the move committed
    gives ``[p, d, q, r]``."""
    hub = build_hub(tmp_path)
    d = _ingest_ready(hub, 1)
    p, q, r = _ingest_backlog(hub, 2), _ingest_backlog(hub, 3), _ingest_backlog(hub, 4)
    stores = chunk_stores(hub.engine, hub.clock)
    with ranking_unit(hub.engine, lock=BACKLOG_RANKING_LOCK) as unit:
        stores.queue.record_backlog_positions(
            [(p, 1.0), (q, math.nextafter(1.0, math.inf)), (r, 2.0)], at=hub.clock.now(), transaction=unit
        )
    names = {"d": d, "p": p, "q": q, "r": r}
    assert _backlog_order(hub, names) == ["p", "q", "r"]

    competitor = _Competitor(lambda: _move(hub, "/api/backlog/position", p, r))
    competitor.hold(monkeypatch, "record_backlog_positions")
    demoted = hub.client.post(f"/api/chunks/{d}/demote", json={"after_chunk_id": p})

    assert demoted.status_code == 202, demoted.text
    assert competitor.finish() == 200
    assert _backlog_order(hub, names) in (["d", "q", "r", "p"], ["q", "r", "p", "d"])
    assert not competitor.committed_inside_window, "the competing move committed inside the demote's unit"


def test_a_whole_order_replace_and_a_move_commit_as_some_serial_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over ``[a, b, c]``, replacing the order with ``c`` first keeps ``a`` before ``b`` as it reads
    them, while "move b to the top" runs. Run serially they give ``[b, c, a]`` or ``[c, b, a]``; a
    replacement computed before the move committed gives ``[c, a, b]``, undoing the move."""
    hub = build_hub(tmp_path)
    a, b, c = _ingest_ready(hub, 1), _ingest_ready(hub, 2), _ingest_ready(hub, 3)
    assert hub.client.put("/api/queue", json={"chunk_ids": [a, b, c]}).status_code == 200

    competitor = _Competitor(lambda: _move(hub, "/api/queue/position", b, None))
    competitor.hold(monkeypatch)
    replaced = hub.client.put("/api/queue", json={"chunk_ids": [c]})

    assert replaced.status_code == 200, replaced.text
    assert competitor.finish() == 200
    assert _ready_order(hub, {"a": a, "b": b, "c": c}) in (["b", "c", "a"], ["c", "b", "a"])
    assert not competitor.committed_inside_window, "the competing move committed inside the replace's unit"
