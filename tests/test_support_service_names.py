"""The harness names the two service bags a test reads: ``hub_services`` for the hub-level bag on
``app.state.services``, and ``tenant_services_of(app)`` for the default tenant's bag a harness's
``services`` also holds."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, tenant_services_of

pytestmark = pytest.mark.component


def test_hub_services_is_the_apps_hub_level_bag(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None

    assert hub.hub_services is hub.app.state.services


def test_tenant_services_of_an_app_is_the_harness_services(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None

    assert tenant_services_of(hub.app) is hub.services
