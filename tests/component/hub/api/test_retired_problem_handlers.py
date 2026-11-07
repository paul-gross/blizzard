from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from blizzard.hub.domain.runners.host import HostRetired
from blizzard.hub.domain.runners.registration import RunnerRetired
from tests.support.scaffolding import build_hub


@pytest.mark.parametrize(
    ("path", "exc", "problem_type", "title"),
    [
        ("/api/fleet/chunks/example", RunnerRetired("r", action="fleet call"), "runner-retired", "Runner retired"),
        ("/api/fleet/host", HostRetired("h", action="host call"), "host-retired", "Host retired"),
    ],
)
def test_declared_retirement_and_unrelated_path(
    tmp_path: Path, path: str, exc: Exception, problem_type: str, title: str
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    router = APIRouter()

    def refuse() -> None:
        raise exc

    template = "/api/fleet/chunks/{chunk_id}" if "chunks" in path else path
    router.add_api_route(template, refuse, methods=["GET"])
    router.add_api_route("/unrelated", refuse, methods=["GET"])
    hub.app.router.routes[0:0] = router.routes
    client = TestClient(hub.app)

    response = client.get(path)
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": f"urn:blizzard:problem:{problem_type}",
        "title": title,
        "detail": str(exc),
        "instance": path,
        "status": 403,
    }
    response = client.get("/unrelated")
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": str(exc)}
