"""The repository and work-source doors refuse each address field the forge now carries, naming that forge where one can
be told, and refuse a create naming no forge (component tier, in-process hub)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.support.scaffolding import HubHarness, build_hub

_GHE_API = "https://ghe.acme.internal/api/v3"
_GHE_WEB = "https://code.acme.internal"
_REPO: dict[str, Any] = {"slug": "demo", "owner": "acme", "repo": "demo", "base_branch": "main", "secret_name": "gh"}
_SOURCE: dict[str, Any] = {"slug": "demo", "provider": "github", "locator": "acme/demo", "secret": "gh"}


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    """A hub holding two forges — ``ghe``, with its own web origin, and ``other`` — and the secret ``gh``."""
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    for forge in (
        {"slug": "ghe", "kind": "github", "api_base": _GHE_API, "web_base": _GHE_WEB},
        {"slug": "other", "kind": "github", "api_base": "https://api.other.example"},
    ):
        assert hub.client.post("/api/forges", json=forge).status_code == 201
    return hub


def _refused(response: Any, field: str) -> str:
    assert response.status_code == 422, response.text
    (detail,) = response.json()["detail"]
    assert (detail["loc"], detail["type"]) == (["body", field], "field_moved_to_forge")
    return detail["msg"]


@pytest.mark.parametrize(
    ("path", "body", "field", "value"),
    [
        ("/api/repositories", _REPO, "forge_api_url", "HTTPS://GHE.acme.internal:443/api/v3/"),
        ("/api/work-sources", _SOURCE, "api_base", _GHE_API),
        ("/api/work-sources", _SOURCE, "web_base", f"{_GHE_WEB}/"),
    ],
)
def test_a_create_sending_an_address_field_is_refused_naming_the_forge_holding_its_origin(
    hub: HubHarness, path: str, body: dict[str, Any], field: str, value: str
) -> None:
    assert "forge ghe" in _refused(hub.client.post(path, json={**body, field: value}), field)
    assert all(row.get("built_in") for row in hub.client.get("/api/work-sources").json()["sources"])
    assert hub.client.get("/api/repositories").json()["repositories"] == []


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [("/api/repositories", _REPO, "forge_api_url"), ("/api/work-sources", _SOURCE, "api_base")],
)
def test_a_create_naming_a_forge_and_an_address_field_is_refused_naming_the_forge_it_names(
    hub: HubHarness, path: str, body: dict[str, Any], field: str
) -> None:
    message = _refused(hub.client.post(path, json={**body, "forge": "other", field: _GHE_API}), field)
    assert "forge other" in message


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [("/api/repositories", _REPO, "forge_api_url"), ("/api/work-sources", _SOURCE, "web_base")],
)
def test_an_address_no_forge_holds_is_refused_naming_the_field_and_forge(
    hub: HubHarness, path: str, body: dict[str, Any], field: str
) -> None:
    message = _refused(hub.client.post(path, json={**body, field: "https://nobody.example"}), field)
    assert "`forge`" in message and "forge ghe" not in message and "forge other" not in message


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        ("/api/repositories", _REPO, "forge_api_url"),
        ("/api/work-sources", _SOURCE, "api_base"),
        ("/api/work-sources", _SOURCE, "web_base"),
    ],
)
def test_an_edit_sending_an_address_field_is_refused_naming_the_records_forge(
    hub: HubHarness, path: str, body: dict[str, Any], field: str
) -> None:
    created = hub.client.post(path, json={**body, "forge": "ghe"})
    assert created.status_code == 201, created.text
    patched = hub.client.patch(f"{path}/demo", json={field: "https://api.other.example"})
    assert "forge ghe" in _refused(patched, field)
    moved = hub.client.patch(f"{path}/demo", json={"forge": "other", field: _GHE_API})
    assert "forge other" in _refused(moved, field)
    assert hub.client.get(f"{path}/demo").json()["forge_slug"] == "ghe"


@pytest.mark.parametrize(("path", "body"), [("/api/repositories", _REPO), ("/api/work-sources", _SOURCE)])
def test_a_create_naming_no_forge_is_refused_forge_required(hub: HubHarness, path: str, body: dict[str, Any]) -> None:
    refused = hub.client.post(path, json=body)
    assert refused.status_code == 422, refused.text
    (detail,) = refused.json()["detail"]
    assert (detail["loc"], detail["type"]) == (["body", "forge"], "forge_required")


@pytest.mark.parametrize(("path", "body"), [("/api/repositories", _REPO), ("/api/work-sources", _SOURCE)])
def test_a_write_naming_its_forge_answers_no_warnings_and_any_other_field_is_still_forbidden(
    hub: HubHarness, path: str, body: dict[str, Any]
) -> None:
    created = hub.client.post(path, json={**body, "forge": "ghe"})
    assert created.status_code == 201, created.text
    assert created.json()["warnings"] == []
    assert "Deprecation" not in created.headers
    unknown = hub.client.patch(f"{path}/demo", json={"nonsense": 1})
    assert unknown.status_code == 422
    assert unknown.json()["detail"][0]["type"] == "extra_forbidden"
