"""``GET /api/fleet/chunks/{chunk_id}/garden/findings`` — the worker-scoped fleet read
of a routine's live finding bucket (D5, D6, component tier). Derives the routine and
scope from the chunk's own ``RunContext`` rather than a caller-supplied flag, reuses
``findings.py``'s own ``finding_view`` projection, and refuses — rather than answering
an empty bucket for — an unknown chunk or one with no run context at all."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.domain.run_context import RunContext
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import HubHarness, build_hub, hub_store_connections, seed_work_item

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
_ROUTINE = "nightly"
_SCOPE = "blizzard"


def _seed_chunk(hub: HubHarness, *, with_run_context: bool = True) -> str:
    """A work item with its own resting chunk, plus a recorded run context for it
    unless ``with_run_context`` is False — the chunk id the route resolves through."""
    store_connections = hub_store_connections(hub.engine)
    items = WorkItemStore(store_connections)
    item = seed_work_item(items, graph_id="gr_garden", author=WorkItemAuthor.user("u_1"), at=_NOW)
    if with_run_context:
        RunContextStore(store_connections).record(
            item.work_item_id, RunContext(routine_name=_ROUTINE, scope_slug=_SCOPE, mode="dry_run")
        )
    return f"ch_{item.ref}"


def _seed_finding(
    hub: HubHarness,
    finding_id: str,
    *,
    scope_slug: str = _SCOPE,
    routine_name: str = _ROUTINE,
) -> None:
    FindingStore(hub_store_connections(hub.engine)).add(
        finding_id,
        routine_name=routine_name,
        scope_slug=scope_slug,
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=_NOW,
    )


def _resolve_finding(hub: HubHarness, finding_id: str) -> None:
    FindingStore(hub_store_connections(hub.engine)).record_fact(
        finding_id, kind="resolved", at=_NOW, note="fixed", actor="u_1"
    )


def test_404s_on_an_unknown_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/fleet/chunks/ch_ghost/garden/findings")
    assert resp.status_code == 404, resp.text
    assert "unknown chunk" in resp.json()["detail"]


def test_404s_on_a_chunk_with_no_run_context(tmp_path: Path) -> None:
    """A chunk that is not a routine run gets a legible refusal, not an empty list."""
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub, with_run_context=False)
    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/garden/findings")
    assert resp.status_code == 404, resp.text
    assert "no run context" in resp.json()["detail"]


def test_returns_the_scoped_live_bucket_via_the_shared_projection(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2", scope_slug="other-scope")  # a different scope — not in this bucket

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/garden/findings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [row["finding_id"] for row in body] == ["fin_1"]

    # The operator's own `GET /api/findings` reads through the identical projection —
    # the fleet route reuses it rather than restating it.
    operator = hub.client.get("/api/findings", params={"routine": _ROUTINE, "scope": _SCOPE})
    assert body == operator.json()


def test_excludes_an_exited_finding_and_takes_no_include_gone_flag(tmp_path: Path) -> None:
    """D6: the live bucket only, with no ``include_gone`` lever at all."""
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    _resolve_finding(hub, "fin_2")

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/garden/findings")
    assert resp.status_code == 200, resp.text
    assert [row["finding_id"] for row in resp.json()] == ["fin_1"]

    # An `include_gone` query param is not a lever this route recognizes at all — it is
    # silently ignored rather than widening the bucket.
    ignored = hub.client.get(f"/api/fleet/chunks/{chunk_id}/garden/findings", params={"include_gone": "true"})
    assert [row["finding_id"] for row in ignored.json()] == ["fin_1"]


def test_takes_no_routine_or_scope_flag(tmp_path: Path) -> None:
    """The route's own signature carries no such parameter — passing one is a no-op,
    never a way to reach another routine's bucket."""
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _seed_finding(hub, "fin_1")

    resp = hub.client.get(
        f"/api/fleet/chunks/{chunk_id}/garden/findings", params={"routine": "other-routine", "scope": "other-scope"}
    )
    assert resp.status_code == 200, resp.text
    assert [row["finding_id"] for row in resp.json()] == ["fin_1"]
