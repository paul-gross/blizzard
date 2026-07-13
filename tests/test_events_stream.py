"""The hub SSE stub — ``GET /api/events/stream`` is a reserved, terminating stream.

The reserved seam (tech-stack.md live updates): it opens as ``text/event-stream``
and closes cleanly with no events yet. Excluded from OpenAPI (the frontend
subscribes with native ``EventSource``, not the generated client).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.app import create_app_for_export


@pytest.mark.component
def test_events_stream_opens_as_sse() -> None:
    app = create_app_for_export()
    with TestClient(app) as client:
        response = client.get("/api/events/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


@pytest.mark.component
def test_events_stream_excluded_from_openapi() -> None:
    app = create_app_for_export()
    assert "/api/events/stream" not in app.openapi()["paths"]
