"""``GET /api/fleet/system-artifacts`` and ``.../system-artifacts/{name}`` (component tier) —
the hub's own read side of ``ArtifactScope.SYSTEM``, resolved at call time off the packaged
set. Fleet-mounted only, so a plain ``build_hub`` client already carries a fleet-shaped
request; a throwaway ``PackagedSystemArtifacts`` root is injected via ``build_hub``'s own
``system_artifacts`` override (``bzh:dependency-injection``) rather than monkeypatched onto
a module singleton, so the route is exercised without depending on what is actually shipped."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from tests.support import build_hub

pytestmark = pytest.mark.component


def _packaged(tmp_path: Path) -> PackagedSystemArtifacts:
    garden = tmp_path / "garden"
    garden.mkdir(parents=True)
    (garden / "finding-format.md").write_text("the finding format text")
    (tmp_path / "docket.md").write_text("the docket text")
    return PackagedSystemArtifacts(tmp_path)


def test_list_serves_the_full_published_set(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, system_artifacts=_packaged(tmp_path / "packaged"))

    resp = hub.client.get("/api/fleet/system-artifacts")
    assert resp.status_code == 200, resp.text
    body = {item["name"]: item["content"] for item in resp.json()}
    assert body == {"docket": "the docket text", "garden/finding-format": "the finding format text"}


def test_list_is_empty_when_nothing_is_published(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, system_artifacts=PackagedSystemArtifacts(tmp_path / "empty"))

    resp = hub.client.get("/api/fleet/system-artifacts")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_get_resolves_a_slash_bearing_name(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, system_artifacts=_packaged(tmp_path / "packaged"))

    resp = hub.client.get("/api/fleet/system-artifacts/garden/finding-format")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"name": "garden/finding-format", "content": "the finding format text"}


def test_get_404s_for_an_unpublished_name(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, system_artifacts=_packaged(tmp_path / "packaged"))

    resp = hub.client.get("/api/fleet/system-artifacts/ghost")
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]
