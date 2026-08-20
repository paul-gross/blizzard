"""``GET /chunks/{id}/work-items`` renders a hub-owned pointer through the unchanged
handler (issue #357, component tier) — the built-in ``hub`` source needs no
``[[work_source]]`` to resolve at ingest or render at read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import build_hub

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_a_hub_owned_pointer_ingests_and_renders_its_title_and_body(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    item = WorkItemStore(hub.engine).create(
        source="hub",
        title="widget is broken",
        body="steps to repro",
        author=WorkItemAuthor.fleet(),
        stated_priority=None,
        at=_T0,
    )

    chunk_id = hub.client.post("/api/chunks", json={"tokens": [f"hub:{item.ref}"]}).json()["chunk_id"]

    entries = hub.client.get(f"/api/chunks/{chunk_id}/work-items").json()["items"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "hub"
    assert entry["ref"] == item.ref
    assert entry["label"] == f"hub:{item.ref}"
    assert entry["title"] == "widget is broken"
    assert entry["body"] == "steps to repro"
    assert entry["web_url"] == f"/board/chunk/{chunk_id}"
    assert entry["error"] is None
