"""Cadences in a configuration document (component tier, a real hub app): one apply declares a routine and the cadence
that keeps it; a chore naming an unknown routine is accepted and shown as attention; a dry run writes nothing; an
export applies back with no write, as does an entry stating the stored values in another spelling; and a planned
write leaving a cadence promoting its runs needs the permission to admit work, while one that plans none asks
nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.auth_core import Role
from blizzard.hub.api import auth_session
from blizzard.hub.auth.permissions import Permission, permissions_for
from tests.support.scaffolding import HubHarness, build_hub


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _graph(hub: HubHarness) -> str:
    return hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    ).name


def _apply(hub: HubHarness, document: dict[str, Any], *, dry_run: bool = False) -> Any:
    return hub.client.post("/api/config/apply", params={"dry_run": dry_run}, json={"version": 1, **document})


def _cadences(hub: HubHarness) -> list[dict[str, Any]]:
    resp = hub.client.get("/api/cadences", params={"include_retired": True})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _outcomes(resp: Any, kind: str = "cadence") -> list[tuple[str, str]]:
    assert resp.status_code == 200, resp.text
    return [(o["key"], o["op"]) for o in resp.json()["outcomes"] if o["kind"] == kind]


def _declaring(hub: HubHarness, **cadence: Any) -> dict[str, Any]:
    """A document declaring a scope, a routine sweeping it, and a cadence keeping the routine."""
    return {
        "scopes": [{"slug": "blizzard"}],
        "routines": [{"slug": "arch", "graph_name": _graph(hub), "default_scope_slug": "blizzard"}],
        "cadences": [
            {
                "slug": "evening",
                "schedule": {"cron": "0 20 * * *", "zone": "America/Chicago"},
                "spacing": "10d",
                "chores": [{"key": "arch", "routine": "arch", "scopes": "all", "mode": "delta"}],
                **cadence,
            }
        ],
    }


def test_one_apply_declares_a_routine_and_the_cadence_that_keeps_it(hub: HubHarness) -> None:
    resp = _apply(hub, _declaring(hub))

    assert _outcomes(resp, "routine") == [("arch", "create")]
    assert _outcomes(resp) == [("evening", "create")]
    [cadence] = _cadences(hub)
    assert (cadence["slug"], cadence["spacing_minutes"], cadence["attention"]) == ("evening", 10 * 24 * 60, [])
    assert [(c["key"], c["routine"], c["scopes"], c["mode"]) for c in cadence["chores"]] == [
        ("arch", "arch", "all", "delta")
    ]


def test_a_chore_naming_an_unknown_routine_is_accepted_and_needs_attention(hub: HubHarness) -> None:
    resp = _apply(
        hub, {"cadences": [{"slug": "evening", "spacing": "1d", "chores": [{"key": "g", "routine": "nope"}]}]}
    )

    assert _outcomes(resp) == [("evening", "create")]
    [cadence] = _cadences(hub)
    assert cadence["attention"] == [{"reason": "routine_unknown", "chore": "g", "scope": None}]


def test_an_entry_that_cannot_mean_anything_is_refused_at_its_field(hub: HubHarness) -> None:
    resp = _apply(hub, {"cadences": [{"slug": "evening", "schedule": {"cron": "0 0 * * * *", "zone": "UTC"}}]})

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"][0]["loc"] == ["body", "cadences", 0, "schedule.cron"]
    assert _cadences(hub) == []


def test_a_dry_run_reports_the_same_outcomes_and_writes_nothing(hub: HubHarness) -> None:
    document = _declaring(hub)
    before = hub.client.get("/api/config/changes").json()["total"]

    dry = _apply(hub, document, dry_run=True)

    assert _outcomes(dry) == [("evening", "create")]
    assert dry.json()["apply_id"] is None
    assert _cadences(hub) == []
    assert hub.client.get("/api/config/changes").json()["total"] == before
    assert _outcomes(_apply(hub, document)) == _outcomes(dry)


def test_an_export_applies_back_with_no_write(hub: HubHarness) -> None:
    applied = _apply(
        hub,
        _declaring(
            hub,
            name="Evening sweep",
            spacing="36h",
            anchor="finished",
            chores=[
                {"key": "arch", "routine": "arch", "scopes": ["blizzard"], "note": "weigh it", "promote": False},
                {"key": "tests", "routine": "arch"},
            ],
        ),
    )
    assert applied.status_code == 200, applied.text
    retired = _apply(hub, {"cadences": [{"slug": "gone", "spacing": "1d"}]})
    assert retired.status_code == 200, retired.text
    [gone] = [c for c in _cadences(hub) if c["slug"] == "gone"]
    assert hub.client.post(f"/api/cadences/{gone['cadence_id']}/retire", json={}).status_code == 202
    before = hub.client.get("/api/config/changes").json()["total"]

    exported = hub.client.get("/api/config/export")
    assert exported.status_code == 200, exported.text
    document = exported.json()
    assert [c["slug"] for c in document["cadences"]] == ["evening"]
    assert document["cadences"][0]["spacing"] == "36h"

    back = hub.client.post("/api/config/apply", json=document)

    assert _outcomes(back) == [("evening", "unchanged")]
    assert hub.client.get("/api/config/changes").json()["total"] == before


def test_an_entry_stating_the_stored_values_in_another_spelling_writes_nothing(hub: HubHarness) -> None:
    assert _apply(hub, _declaring(hub)).status_code == 200

    again = _apply(
        hub,
        _declaring(
            hub,
            name="evening",
            spacing="14400m",
            anchor="started",
            chores=[{"key": "arch", "routine": "arch", "scopes": "all", "mode": "delta", "promote": True}],
        ),
    )

    assert _outcomes(again) == [("evening", "unchanged")]


def test_a_promoting_cadence_write_needs_chunk_control_and_an_unchanged_entry_asks_nothing(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _declaring(hub)
    assert _apply(hub, document).status_code == 200

    def narrowed(role: Role | None, hub_admin: bool) -> frozenset[Permission]:
        return permissions_for(role, hub_admin) - {Permission.CHUNK_CONTROL}

    monkeypatch.setattr(auth_session, "permissions_for", narrowed)

    assert _outcomes(_apply(hub, document)) == [("evening", "unchanged")]
    refused = _apply(
        hub, {"cadences": [{"slug": "late", "spacing": "1d", "chores": [{"key": "a", "routine": "arch"}]}]}
    )
    assert refused.status_code == 403, refused.text
    assert [c["slug"] for c in _cadences(hub)] == ["evening"]
    held = {"slug": "late", "spacing": "1d", "chores": [{"key": "a", "routine": "arch", "promote": False}]}
    assert _outcomes(_apply(hub, {"cadences": [held]})) == [("late", "create")]
