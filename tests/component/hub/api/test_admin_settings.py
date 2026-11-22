"""Hub settings routes over the real hub and its global settings store."""

from __future__ import annotations

import tomllib
from pathlib import Path

from blizzard.auth_core import Role
from blizzard.hub.config import AUTH_MODE_OAUTH, json_literal
from blizzard.hub.domain.observability.transcripts import TranscriptCaps
from blizzard.hub.settings.footing import HubSettingsFooting
from blizzard.hub.settings.model import IReadHubSettingsAdmin
from blizzard.hub.settings.reader import HubSettingsReader
from blizzard.hub.settings.service import HubSettingsAuthoring
from blizzard.wire import problem as problem_types
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    seed_hub_admin,
    seed_session,
    seed_user,
)


def _hub(tmp_path: Path, auth_mode: str = "none") -> HubHarness:
    return build_hub(tmp_path, auth_mode=auth_mode, with_settings=True)


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


def test_nonfinite_settings_are_rejected_without_changing_export(tmp_path: Path) -> None:
    client = _hub(tmp_path).client
    path = "/api/admin/settings"
    headers = {"If-Match": "0", "Content-Type": "application/json"}
    for number in ("1e309", "-1e309", "NaN", "Infinity", "-Infinity"):
        patch = client.patch(
            path, headers=headers, content=f'{{"set": {{"auth.login_throttle.refill_seconds": {number}}}}}'
        )
        assert patch.status_code == 422, patch.text
        assert patch.json()["code"] == "settings_invalid"
        for suffix in ("", "?dry_run=true"):
            apply = client.post(
                path + "/apply" + suffix,
                headers=headers,
                content=f'{{"auth": {{"login_throttle": {{"refill_seconds": {number}}}}}}}',
            )
            assert apply.status_code == 422, apply.text
            assert apply.json()["code"] == "settings_invalid"
    assert client.get(path).json()["revision"] == 0
    assert client.get(path + "/export").json() == {}
    assert client.get(path + "/changes").json()["changes"] == []
    valid = client.patch(path, headers={"If-Match": "0"}, json={"set": {"auth.login_throttle.refill_seconds": 1.5}})
    assert valid.status_code == 200
    assert client.get(path + "/export").json() == {"auth": {"login_throttle": {"refill_seconds": 1.5}}}


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


def test_changes_resolve_absent_and_at_default_sides_to_effective_values(tmp_path: Path) -> None:
    client = _hub(tmp_path).client
    path = "/api/admin/settings"
    assert client.post(path + "/apply", json={"action_pass_seconds": 5}).status_code == 200
    assert client.patch(path, headers={"If-Match": "1"}, json={"unset": ["action_pass_seconds"]}).status_code == 200
    unset, applied = client.get(path + "/changes").json()["changes"]
    assert applied["diff"] == [
        {"field": "action_pass_seconds", "old": 300, "new": 5, "old_label": None, "new_label": None}
    ]
    assert unset["diff"] == [
        {"field": "action_pass_seconds", "old": 5, "new": 300, "old_label": None, "new_label": None}
    ]


def test_transcript_caps_show_effective_defaults_and_overrides(tmp_path: Path) -> None:
    client = _hub(tmp_path).client
    path = "/api/admin/settings"
    defaults = TranscriptCaps()
    rows = {r["key"]: r for r in client.get(path).json()["settings"]}
    for name in ("record_max_bytes", "chunk_budget_max_bytes", "runner_daily_rate_max_bytes"):
        row = rows[f"transcripts.{name}"]
        assert (row["value"], row["source"]) == (getattr(defaults, name), "default")
    response = client.post(path + "/apply", json={"transcripts": {"record_max_bytes": 1024}})
    diff = response.json()["outcomes"][0]["diff"][0]
    assert (diff["old"], diff["new"]) == (defaults.record_max_bytes, 1024)
    response = client.patch(path, headers={"If-Match": "1"}, json={"unset": ["transcripts.record_max_bytes"]})
    row = next(r for r in response.json()["settings"] if r["key"] == "transcripts.record_max_bytes")
    assert (row["value"], row["source"]) == (defaults.record_max_bytes, "default")


def test_get_omits_and_patch_refuses_a_grouping_key(tmp_path: Path) -> None:
    client = _hub(tmp_path).client
    path = "/api/admin/settings"
    assert "transcripts" not in {r["key"] for r in client.get(path).json()["settings"]}
    refused = client.patch(path, headers={"If-Match": "1"}, json={"set": {"transcripts": {}}})
    assert refused.status_code == 422
    assert (refused.json()["type"], refused.json()["code"]) == (
        "urn:blizzard:problem:setting-unknown",
        "setting_unknown",
    )
    boot = client.patch(path, headers={"If-Match": "1"}, json={"set": {"host": "x"}})
    assert (boot.json()["type"], boot.json()["code"]) == (
        "urn:blizzard:problem:setting-not-live",
        "setting_not_live",
    )


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


def test_the_settings_ports_are_the_members_of_the_one_borrowed_graph(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    graph = hub.wired_app.state.hub_settings
    resolver = hub.hub_services.process.resolver
    assert resolver.get(IReadHubSettingsAdmin) is graph.store
    assert resolver.get(HubSettingsReader) is graph.reader
    assert resolver.get(HubSettingsAuthoring) is graph.authoring
    assert resolver.get(HubSettingsFooting) is graph.footing


_SETTINGS_ROUTES: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "", None),
    ("GET", "/changes", None),
    ("GET", "/export", None),
    ("PATCH", "", {}),
    ("POST", "/apply", {}),
]


def test_a_settings_less_hub_answers_an_admin_the_store_unavailable_problem_and_refuses_others_first(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    member = seed_user(hub, username="tenant-admin", role=Role.ADMIN)
    admin = seed_user(hub, username="hub-admin", role=Role.PENDING)
    seed_hub_admin(hub.engine, admin.user_id)
    member_headers = {"Authorization": f"Bearer {seed_session(hub, member)}"}
    admin_headers = {"Authorization": f"Bearer {seed_session(hub, admin)}"}
    for method, suffix, body in _SETTINGS_ROUTES:
        path = "/api/admin/settings" + suffix
        unavailable = hub.client.request(method, path, headers=admin_headers, json=body)
        assert unavailable.status_code == 503, (method, path, unavailable.text)
        assert unavailable.headers["content-type"].startswith("application/problem+json")
        assert unavailable.json() == {
            "type": problem_types.HUB_STORE_UNAVAILABLE,
            "title": "Hub store unavailable",
            "detail": "hub store not wired — start via `blizzard hub host`",
            "instance": path,
            "status": 503,
        }
        assert hub.client.request(method, path, headers=member_headers, json=body).status_code == 403
        assert hub.client.request(method, path, json=body).status_code in (401, 403)
