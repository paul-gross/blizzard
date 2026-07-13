"""PM pass-through read (D-047) — body + comments, never stored (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import FakePmSource, build_hub

pytestmark = pytest.mark.component

_POINTER = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/42"}


def test_pm_item_reads_body_and_comments_from_the_forge(tmp_path: Path) -> None:
    pm = FakePmSource(body="please fix the flake", comments=["seen it too", "repro attached"])
    hub = build_hub(tmp_path, pm=pm)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]

    resp = hub.client.get(f"/api/chunks/{chunk_id}/pm-item")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "github"
    assert body["url"] == _POINTER["url"]
    assert body["body"] == "please fix the flake"
    assert body["comments"] == ["seen it too", "repro attached"]
    assert body["fetched_at"]
    # The read went to the forge for this pointer — contents are fetched, not stored.
    assert pm.fetched == [_POINTER["url"]]


def test_pm_item_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.get("/api/chunks/ch_missing/pm-item").status_code == 404
