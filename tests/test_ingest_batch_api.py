"""``POST /api/chunks`` over a batch's shape — an empty batch refused, a repeated item wrapped once."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_P1 = {"source": "default", "ref": "1"}
_P2 = {"source": "default", "ref": "2"}


def test_an_empty_batch_is_a_422_and_mints_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/chunks", json={"tokens": []})

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "at least one token required"
    assert hub.client.get("/api/chunks").json()["chunks"] == []
    # Refused before the default graph resolves, so none was minted for it.
    assert hub.services.graphs.list_summaries() == []


def test_two_tokens_naming_one_item_wrap_it_once_in_first_seen_order(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/chunks", json={"tokens": ["default:2", "default#1", "default:2", "default:1"]})

    assert resp.status_code == 201, resp.text
    detail = hub.client.get(f"/api/chunks/{resp.json()['chunk_id']}").json()
    assert [{"source": r["source"], "ref": r["ref"]} for r in detail["work_refs"]] == [_P2, _P1]
