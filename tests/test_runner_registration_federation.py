"""Runner registration's optional ``url``/``redirect_uris`` extension (issue #95).

Federation identity rides the same authenticated write as every other registration
field: an unauthenticated attempt to set or change it is rejected exactly like an
unauthenticated re-registration is, once #86 is enforced."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component


def _register(hub: HubHarness, **kwargs: object) -> Response:
    client: TestClient = hub.client
    body = {"runner_id": "runner-a", "workspace_id": "ws-a", **kwargs}
    return client.post("/api/fleet/runners", json=body)


def test_registration_persists_url_and_redirect_uris(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = _register(hub, url="https://runner-a.example", redirect_uris=["https://runner-a.example/api/auth/callback"])
    assert resp.status_code == 201, resp.text

    registration = hub.services.registry.get_runner("runner-a")
    assert registration is not None
    assert registration.public_url == "https://runner-a.example"
    assert registration.redirect_uris == ("https://runner-a.example/api/auth/callback",)


def test_registration_without_url_leaves_it_null(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = _register(hub)
    assert resp.status_code == 201, resp.text

    registration = hub.services.registry.get_runner("runner-a")
    assert registration is not None
    assert registration.public_url is None
    assert registration.redirect_uris == ()


def test_reregistration_converges_a_changed_redirect_uri(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub, url="https://old.example", redirect_uris=["https://old.example/api/auth/callback"])
    _register(hub, url="https://new.example", redirect_uris=["https://new.example/api/auth/callback"])

    registration = hub.services.registry.get_runner("runner-a")
    assert registration is not None
    assert registration.public_url == "https://new.example"
    assert registration.redirect_uris == ("https://new.example/api/auth/callback",)


def test_unauthenticated_registration_with_redirect_uris_is_rejected_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    resp = _register(hub, url="https://evil.example", redirect_uris=["https://evil.example/api/auth/callback"])
    assert resp.status_code == 401

    registration = hub.services.registry.get_runner("runner-a")
    assert registration is None


def test_registration_persists_the_capability_snapshot(tmp_path: Path) -> None:
    """blizzard#433 — a request naming a harness/tier snapshot stores it whole."""
    hub = build_hub(tmp_path)
    capabilities = [
        {
            "harness_id": "claude_code",
            "version": "1.2.3",
            "tiers": ["blizzard:frontier", "blizzard:basic"],
            "default": True,
        }
    ]
    resp = _register(hub, capabilities=capabilities)
    assert resp.status_code == 201, resp.text

    registration = hub.services.registry.get_runner("runner-a")
    assert registration is not None
    assert len(registration.capabilities) == 1
    capability = registration.capabilities[0]
    assert capability.harness_id == "claude_code"
    assert capability.version == "1.2.3"
    assert capability.tiers == ("blizzard:frontier", "blizzard:basic")
    assert capability.default is True


def test_registration_without_capabilities_leaves_it_empty(tmp_path: Path) -> None:
    """A request predating blizzard#433 (or a harness-less runner) parses unchanged."""
    hub = build_hub(tmp_path)
    resp = _register(hub)
    assert resp.status_code == 201, resp.text

    registration = hub.services.registry.get_runner("runner-a")
    assert registration is not None
    assert registration.capabilities == ()


def test_reregistration_drops_a_removed_binding_from_the_snapshot(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(
        hub,
        capabilities=[{"harness_id": "claude_code", "version": "1.0.0", "tiers": ["blizzard:basic"], "default": True}],
    )

    _register(hub)

    registration = hub.services.registry.get_runner("runner-a")
    assert registration is not None
    assert registration.capabilities == ()
