"""The marker and delivery routes a hub node's ``run:`` script called are gone (component tier): none is in the
route inventory, and a person allowed to control the chunk reaches no route on any of them — the board's static mount
answers the POST 405, and 404 stands where no mount is served."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from tests.support.hub_request_auth import ingest_chunk
from tests.support.scaffolding import build_hub

_REMOVED = (
    "/api/chunks/{chunk_id}/hub-markers",
    "/api/chunks/{chunk_id}/garden-delivery",
    "/api/chunks/{chunk_id}/review-findings-delivery",
)


def test_the_removed_routes_are_absent_from_the_route_inventory(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    app = hub.client.app
    assert isinstance(app, FastAPI)
    paths = app.openapi()["paths"]

    assert [path for path in _REMOVED if path in paths] == []


def test_each_removed_route_reaches_no_route_on_a_chunk_that_exists(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest_chunk(hub)

    answers = {
        path: hub.client.post(
            path.format(chunk_id=chunk_id), params={"node_id": "nd_x", "epoch": 1}, json={"name": "m"}
        ).status_code
        for path in _REMOVED
    }

    assert {path: status in (404, 405) for path, status in answers.items()} == dict.fromkeys(_REMOVED, True), answers
