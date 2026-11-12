"""Hub settings routes over the real hub and its global settings store."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.toml_emit import json_literal
from blizzard.hub.composition import build_hub_settings
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.settings.footing import HubSettingsFooting
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    hub_level_connections,
    seed_hub_admin,
    seed_session,
    seed_user,
)


def _hub(tmp_path: Path, auth_mode: str = "none") -> HubHarness:
    hub = build_hub(tmp_path, auth_mode=auth_mode)
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    hub.wired_app.state.hub_settings = build_hub_settings(
        hub_level_connections(hub.engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), footing
    )
    return hub


def test_change_notifications_only_follow_commits(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    notifications: list[frozenset[str]] = []
    unsubscribe = hub.wired_app.state.hub_settings.authoring.subscribe(notifications.append)
    client = hub.client
    path = "/api/admin/settings"
    assert client.post(path + "/apply?dry_run=true", json={"annotation_interval_seconds": 7}).status_code == 200
    assert client.post(path + "/apply", json={"annotation_interval_seconds": -1}).status_code == 422
    assert notifications == []
    assert client.post(path + "/apply", json={"annotation_interval_seconds": 7}).status_code == 200
    assert notifications == [frozenset({"annotation_interval_seconds"})]
    assert client.post(path + "/apply", json={"annotation_interval_seconds": 7}).status_code == 200
    assert len(notifications) == 1
    unsubscribe()


def test_read_write_history_apply_and_round_trip(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    client = hub.client
    path = "/api/admin/settings"
    initial = client.get(path)
    assert initial.status_code == 200, initial.text
    assert initial.json()["revision"] == 0
    assert "in_force" not in initial.json()
    assert all(row["lifetime"] == "pass" for row in initial.json()["settings"])
    assert client.patch(path, json={"set": {"action_pass_seconds": 5}}).status_code == 409
    changed = client.patch(path, headers={"If-Match": "0"}, json={"set": {"action_pass_seconds": 5}})
    assert changed.status_code == 200, changed.text
    row = next(r for r in changed.json()["settings"] if r["key"] == "action_pass_seconds")
    assert (row["value"], row["source"], row["revision"], row["written_by"]) == (5, "entry", 1, "operator")
    assert client.patch(path, headers={"If-Match": "0"}, json={"unset": ["action_pass_seconds"]}).status_code == 409
    boot = client.patch(path, headers={"If-Match": "1"}, json={"set": {"host": "x"}})
    assert boot.status_code == 422 and boot.json()["code"] == "setting_not_live"
    unknown = client.patch(path, headers={"If-Match": "1"}, json={"set": {"bad": 1}})
    assert unknown.status_code == 422 and unknown.json()["code"] == "setting_unknown"
    invalid = client.patch(path, headers={"If-Match": "1"}, json={"set": {"action_pass_seconds": -1}})
    assert invalid.status_code == 422 and invalid.json()["code"] == "settings_invalid"
    assert invalid.json()["errors"]
    assert (
        client.patch(
            path, headers={"If-Match": "1"}, json={"set": {"action_pass_seconds": 1}, "unset": ["action_pass_seconds"]}
        ).status_code
        == 422
    )
    preview = client.post(path + "/apply?dry_run=true", json={"action_pass_seconds": 6})
    assert preview.status_code == 200, preview.text
    assert preview.json()["outcomes"][0]["op"] == "edit"
    assert client.get(path).json()["revision"] == 1
    assert len(client.get(path + "/changes").json()["changes"]) == 1
    rejected = client.post(path + "/apply", json={"action_pass_seconds": 6, "auth": {"mode": "oauth"}})
    assert rejected.status_code == 422 and rejected.json()["code"] == "setting_not_live"
    invalid_apply = client.post(path + "/apply", json={"action_pass_seconds": -1})
    assert invalid_apply.status_code == 422 and client.get(path).json()["revision"] == 1
    applied = client.post(path + "/apply", json={"action_pass_seconds": 6})
    assert applied.status_code == 200, applied.text
    assert applied.json()["apply_id"]
    unset = client.patch(path, headers={"If-Match": "2"}, json={"unset": ["action_pass_seconds"]})
    assert unset.status_code == 200, unset.text
    exported = client.get(path + "/export").json()
    assert exported == {}
    # The route's JSON is encodable as the same TOML document the CLI sends back.
    toml = "\n".join(f"{key} = {json_literal(value)}" for key, value in exported.items())
    decoded = tomllib.loads(toml)
    preview_roundtrip = client.post(path + "/apply?dry_run=true", json=decoded)
    assert preview_roundtrip.json()["outcomes"] == []
    reapply = client.post(path + "/apply", json=decoded)
    assert reapply.status_code == 200, reapply.text
    assert reapply.json()["outcomes"] == []
    assert client.get(path).json()["revision"] == 3
    first = client.get(path + "/changes?limit=2").json()
    assert [r["revision"] for r in first["changes"]] == [3, 2]
    assert first["changes"][1]["door"] == "apply"
    assert [r["revision"] for r in client.get(path + f"/changes?before={first['next_before']}").json()["changes"]] == [
        1
    ]
    # PATCH still sets explicitly even when the requested value equals the default.
    explicit = client.patch(path, headers={"If-Match": "3"}, json={"set": {"action_pass_seconds": 300}})
    assert explicit.status_code == 200
    row = next(r for r in explicit.json()["settings"] if r["key"] == "action_pass_seconds")
    assert row["source"] == "entry" and row["revision"] == 4
    applied_default = client.post(path + "/apply", json={"annotation_interval_seconds": 120})
    assert applied_default.status_code == 200 and applied_default.json()["outcomes"][0]["op"] == "edit"
    row = next(r for r in client.get(path).json()["settings"] if r["key"] == "annotation_interval_seconds")
    assert row["source"] == "entry" and row["revision"] == 5


def test_export_omits_tombstones_and_apply_preserves_omitted_values(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "other").mkdir()
    client = _hub(tmp_path / "source").client
    path = "/api/admin/settings"
    unset = ["action_pass_seconds", "transcripts.record_max_bytes"]
    assert client.patch(path, headers={"If-Match": "0"}, json={"unset": unset}).status_code == 200
    assert (
        client.patch(path, headers={"If-Match": "1"}, json={"set": {"annotation_interval_seconds": 120}}).status_code
        == 200
    )
    document = client.get(path + "/export").json()
    assert document == {"annotation_interval_seconds": 120}
    preview = client.post(path + "/apply?dry_run=true", json=document)
    assert preview.json()["outcomes"][0]["op"] == "unchanged"
    assert client.get(path).json()["revision"] == 2
    applied = client.post(path + "/apply", json=document)
    assert applied.json()["outcomes"][0]["op"] == "unchanged"
    assert client.get(path).json()["revision"] == 2

    other = _hub(tmp_path / "other").client
    assert (
        other.patch(
            path,
            headers={"If-Match": "0"},
            json={"set": {"action_pass_seconds": 7, "transcripts.record_max_bytes": 1024}},
        ).status_code
        == 200
    )
    assert other.post(path + "/apply", json=document).status_code == 200
    rows = {r["key"]: r for r in other.get(path).json()["settings"]}
    assert rows["action_pass_seconds"]["value"] == 7
    assert rows["transcripts.record_max_bytes"]["value"] == 1024
    rejected = other.post(path + "/apply", json={"action_pass_seconds": None})
    assert rejected.status_code == 422 and rejected.json()["code"] == "settings_invalid"
    assert other.get(path).json()["revision"] == 2
    default_set = client.post(path + "/apply", json={"action_pass_seconds": 300})
    assert default_set.status_code == 200 and default_set.json()["outcomes"][0]["op"] == "edit"
    row = next(r for r in client.get(path).json()["settings"] if r["key"] == "action_pass_seconds")
    assert (row["source"], row["value"]) == ("entry", 300)


def test_apply_diff_resolves_defaults_for_absent_and_unset_entries(tmp_path: Path) -> None:
    client = _hub(tmp_path).client
    path = "/api/admin/settings"
    for suffix in ("?dry_run=true", ""):
        response = client.post(path + "/apply" + suffix, json={"action_pass_seconds": 5})
        diff = response.json()["outcomes"][0]["diff"][0]
        assert (diff["field"], diff["old"], diff["new"]) == ("action_pass_seconds", 300, 5)
    assert client.patch(path, headers={"If-Match": "1"}, json={"unset": ["action_pass_seconds"]}).status_code == 200
    response = client.post(path + "/apply", json={"action_pass_seconds": 6})
    diff = response.json()["outcomes"][0]["diff"][0]
    assert (diff["field"], diff["old"], diff["new"]) == ("action_pass_seconds", 300, 6)


def test_request_validation_matches_declared_problem(tmp_path: Path) -> None:
    client = _hub(tmp_path).client
    path = "/api/admin/settings"
    cases: list[tuple[str, str, object, dict[str, str]]] = [
        ("PATCH", "", {"unset": "bad"}, {}),
        ("PATCH", "", {}, {"If-Match": "bad"}),
        ("POST", "/apply", [], {}),
    ]
    for method, suffix, body, headers in cases:
        response = client.request(method, path + suffix, json=body, headers=headers)
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "settings_invalid"
    response = client.get(path + "/changes?limit=bad")
    assert response.status_code == 422 and "detail" in response.json()


def test_all_routes_require_hub_administrator(tmp_path: Path) -> None:
    hub = _hub(tmp_path, AUTH_MODE_OAUTH)
    member = seed_user(hub, username="tenant-admin", role=Role.ADMIN)
    admin = seed_user(hub, username="hub-admin", role=Role.PENDING)
    seed_hub_admin(hub.engine, admin.user_id)
    member_headers = {"Authorization": f"Bearer {seed_session(hub, member)}"}
    admin_headers = {"Authorization": f"Bearer {seed_session(hub, admin)}"}
    path = "/api/admin/settings"
    cases: list[tuple[str, str, dict[str, object] | None]] = [
        ("GET", "", None),
        ("GET", "/changes", None),
        ("GET", "/export", None),
        ("PATCH", "", {}),
        ("POST", "/apply", {}),
    ]
    for method, suffix, body in cases:
        forbidden = hub.client.request(method, path + suffix, headers=member_headers, json=body)
        assert forbidden.status_code == 403
        assert forbidden.headers["content-type"].startswith("application/problem+json")
        assert forbidden.json()["type"] == "urn:blizzard:problem:permission-required"
        response = hub.client.request(method, path + suffix, headers={**admin_headers, "If-Match": "0"}, json=body)
        assert response.status_code == 200, response.text
