"""A blank secret value is refused on every API door (component tier) — create and replace
answer 422 and write nothing; the refusal echoes no input."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime

pytestmark = pytest.mark.component


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(hub_app.build_hosted_app(hub_runtime.init_environment(tmp_path / "hub"))) as c:
        yield c


@pytest.mark.parametrize("blank", ["", "   "])
def test_create_with_a_blank_value_is_422_and_writes_nothing(client: TestClient, blank: str) -> None:
    refused = client.post("/api/secrets", json={"name": "gh", "value": blank})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "a secret value must not be blank"
    assert client.get("/api/secrets/gh").status_code == 404


@pytest.mark.parametrize("blank", ["", "   "])
def test_replace_with_a_blank_value_is_422_and_keeps_the_revision(client: TestClient, blank: str) -> None:
    assert client.post("/api/secrets", json={"name": "gh", "value": "tok"}).status_code == 201
    refused = client.put("/api/secrets/gh/value", json={"value": blank})
    assert refused.status_code == 422
    assert client.get("/api/secrets/gh").json()["revision"] == 1
