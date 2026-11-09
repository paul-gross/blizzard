"""Urgencies and the tenant's settings in a configuration document — ``POST /api/config/apply`` and
``GET /api/config/export`` with the ``urgencies``, ``urgency_order`` and ``settings`` sections (component tier, wired
hub app)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx2
import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config_apply import service as config_apply_service
from tests.support.scaffolding import HubHarness, build_hub

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _apply(hub: HubHarness, document: dict[str, object], *, dry_run: bool = False) -> httpx2.Response:
    return hub.client.post("/api/config/apply", params={"dry_run": dry_run}, json={"version": 2, **document})


def _rows(applied: httpx2.Response) -> list[tuple[str, str, str]]:
    assert applied.status_code == 200, applied.text
    return [(o["kind"], o["key"], o["op"]) for o in applied.json()["outcomes"]]


def _refused(applied: httpx2.Response) -> tuple[int, list[Any], str]:
    assert applied.status_code == 422, applied.text
    entry = applied.json()["detail"][0]
    return applied.status_code, entry["loc"], entry["type"]


def _slugs(hub: HubHarness) -> list[str]:
    return [u["slug"] for u in hub.client.get("/api/urgencies").json()["urgencies"]]


def _default(hub: HubHarness) -> str:
    return hub.client.get("/api/settings").json()["default_urgency"]["slug"]


def _change_count(hub: HubHarness) -> int:
    return hub.client.get("/api/config/changes", params={"limit": 1}).json()["total"]


def _create(hub: HubHarness, slug: str) -> None:
    assert hub.client.post("/api/urgencies", json={"slug": slug}).status_code == 201


def _retire(hub: HubHarness, slug: str) -> None:
    response = hub.client.post(f"/api/urgencies/{slug}/retire", json={})
    assert response.status_code == 200, response.text


def test_one_document_declares_an_urgency_orders_it_and_makes_it_the_default(hub: HubHarness) -> None:
    applied = _apply(
        hub,
        {
            "urgencies": [{"slug": "urgent", "color": "red", "emoji": "🔥"}, {"slug": "standard"}],
            "urgency_order": ["urgent", "standard"],
            "settings": {"default_urgency": "urgent"},
        },
    )

    assert _rows(applied) == [
        ("urgency", "urgent", "create"),
        ("urgency", "standard", "unchanged"),
        ("urgency_order", "urgency_order", "edit"),
        ("tenant_settings", "settings", "edit"),
    ]
    assert _slugs(hub) == ["urgent", "standard"]
    assert _default(hub) == "urgent"
    apply_id = applied.json()["apply_id"]
    kinds = [
        c["record_kind"]
        for c in hub.client.get("/api/config/changes", params={"limit": 50}).json()["changes"]
        if c["apply_id"] == apply_id
    ]
    assert sorted(kinds) == ["tenant_settings", "urgency", "urgency_order"]


def test_a_new_urgency_lands_at_the_least_urgent_end_when_the_order_is_omitted(hub: HubHarness) -> None:
    _rows(_apply(hub, {"urgencies": [{"slug": "later"}, {"slug": "never"}]}))

    assert _slugs(hub) == ["standard", "later", "never"]
    assert _default(hub) == "standard"


def test_an_apply_naming_a_retired_urgency_is_refused_and_never_enables_it(hub: HubHarness) -> None:
    _create(hub, "later")
    _retire(hub, "later")
    before = _change_count(hub)

    documents: list[tuple[dict[str, object], list[object]]] = [
        ({"urgencies": [{"slug": "later", "name": "Later"}]}, ["body", "urgencies", 0, "slug"]),
        ({"urgency_order": ["standard", "later"]}, ["body", "urgency_order", 1]),
        ({"settings": {"default_urgency": "later"}}, ["body", "settings", 0, "default_urgency"]),
    ]
    for document, loc in documents:
        assert _refused(_apply(hub, document)) == (422, loc, "urgency_retired")

    assert _slugs(hub) == ["standard"]
    assert _change_count(hub) == before


def test_a_reference_to_an_urgency_neither_stored_nor_declared_is_unknown(hub: HubHarness) -> None:
    assert _refused(_apply(hub, {"settings": {"default_urgency": "nope"}}))[2] == "urgency_unknown"


def test_an_order_leaving_an_urgency_out_is_refused_incomplete(hub: HubHarness) -> None:
    _create(hub, "later")
    before = _change_count(hub)

    status, _, code = _refused(_apply(hub, {"urgencies": [{"slug": "now"}], "urgency_order": ["now", "standard"]}))

    assert (status, code) == (422, "urgency_order_incomplete")
    assert _slugs(hub) == ["standard", "later"]
    assert _change_count(hub) == before


def test_a_dry_run_reaches_the_same_refusals_and_writes_nothing(hub: HubHarness) -> None:
    _create(hub, "later")
    _retire(hub, "later")
    before = _change_count(hub)

    assert _refused(_apply(hub, {"urgencies": [{"slug": "later"}]}, dry_run=True))[2] == "urgency_retired"
    assert (
        _refused(_apply(hub, {"urgencies": [{"slug": "now"}], "urgency_order": ["standard"]}, dry_run=True))[2]
        == "urgency_order_incomplete"
    )
    dry = _apply(hub, {"urgencies": [{"slug": "now"}], "settings": {"default_urgency": "now"}}, dry_run=True)
    assert _rows(dry) == [("urgency", "now", "create"), ("tenant_settings", "settings", "edit")]
    assert dry.json()["apply_id"] is None
    assert _slugs(hub) == ["standard"]
    assert _default(hub) == "standard"
    assert _change_count(hub) == before


def test_an_export_carries_the_icon_bytes_and_applied_back_writes_nothing(hub: HubHarness) -> None:
    icon = {"media_type": "image/png", "data": base64.b64encode(_PNG).decode()}
    _rows(
        _apply(
            hub,
            {
                "urgencies": [{"slug": "urgent", "color": "#aa0000", "icon": icon}, {"slug": "later", "emoji": "🐢"}],
                "urgency_order": ["urgent", "standard", "later"],
            },
        )
    )
    _create(hub, "gone")
    _retire(hub, "gone")

    document = hub.client.get("/api/config/export").json()

    assert document["urgencies"] == [
        {"slug": "urgent", "name": "urgent", "color": "#aa0000", "emoji": None, "icon": icon},
        {"slug": "standard", "name": "standard", "color": "slate", "emoji": None, "icon": None},
        {"slug": "later", "name": "later", "color": document["urgencies"][2]["color"], "emoji": "🐢", "icon": None},
    ]
    assert document["urgency_order"] == ["urgent", "standard", "later"]
    assert document["settings"] == {"default_urgency": "standard"}
    assert "data" not in hub.client.get("/api/urgencies/urgent").json()["icon"]
    before = _change_count(hub)
    replay = hub.client.post("/api/config/apply", json=document)
    assert {o["op"] for o in replay.json()["outcomes"]} == {"unchanged"}
    assert _change_count(hub) == before


def test_a_retire_committing_between_the_plan_and_the_write_refuses_the_apply_as_stale(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(hub, "later")
    planned = config_apply_service.plan_urgencies

    def plan_then_retire(*args: Any, **kwargs: Any) -> Any:
        plan = planned(*args, **kwargs)
        later = hub.services.urgency_reading.resolve("later")
        assert later is not None
        hub.services.urgency_lifecycle.retire(
            later, ChangeContext(actor="rival", door=Door.API), move_to=None, may_control=True, if_match=None
        )
        return plan

    monkeypatch.setattr(config_apply_service, "plan_urgencies", plan_then_retire)
    applied = _apply(hub, {"urgency_order": ["later", "standard"]})

    assert applied.status_code == 409, applied.text
    assert _slugs(hub) == ["standard"]


def test_the_urgencies_and_settings_schemas_are_served(hub: HubHarness) -> None:
    for kind, field in (("urgencies", "slug"), ("settings", "default_urgency")):
        response = hub.client.get(f"/api/config/schema/{kind}")
        assert response.status_code == 200, response.text
        assert field in response.json()["properties"]


def test_an_apply_that_reorders_publishes_the_queue_change_and_one_that_does_not_publishes_none(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(hub, "later")
    published: list[None] = []
    monkeypatch.setattr(hub.services.events, "publish_queue_changed", lambda: published.append(None))

    assert _apply(hub, {"urgency_order": ["later", "standard"]}, dry_run=True).status_code == 200
    assert _apply(hub, {"urgency_order": ["standard", "later"]}).status_code == 200
    assert published == []
    assert _apply(hub, {"urgency_order": ["later", "standard"]}).status_code == 200
    assert published == [None]
