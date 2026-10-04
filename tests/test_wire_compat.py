"""blizzard:wire-compat (bzh:fleet-wire-additive) — hand-built fixture specs, never the
real committed one; the git-history walk uses a real throwaway repo, per
``tests/test_runner_basic_provider.py``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from blizzard.tools import wire_compat
from blizzard.tools.wire_compat import WireCompatError, check_history, classify_spec_diff

pytestmark = pytest.mark.unit


# --- fixture-spec helpers -----------------------------------------------------------


def _spec(paths: dict, schemas: dict | None = None) -> dict:
    return {"openapi": "3.1.0", "paths": paths, "components": {"schemas": schemas or {}}}


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _op(*, response_schema: dict | None = None, request_schema: dict | None = None) -> dict:
    op: dict = {"responses": {}}
    if response_schema is not None:
        op["responses"]["200"] = {"content": {"application/json": {"schema": response_schema}}}
    else:
        op["responses"]["204"] = {"description": "no content"}
    if request_schema is not None:
        op["requestBody"] = {"content": {"application/json": {"schema": request_schema}}}
    return op


def _violation_texts(violations) -> list[str]:
    return [str(v) for v in violations]


# --- surface honored ------------------------------------------------------------------


def test_new_route_is_additive() -> None:
    base = _spec({})
    head = _spec({"/api/fleet/widgets": {"get": _op()}})
    assert classify_spec_diff(base, head) == []


def test_surface_path_removed_is_breaking() -> None:
    base = _spec({"/api/fleet/widgets": {"get": _op()}})
    head = _spec({})
    assert any("surface path removed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_surface_method_removed_is_breaking() -> None:
    base = _spec({"/api/fleet/widgets": {"get": _op(), "post": _op()}})
    head = _spec({"/api/fleet/widgets": {"get": _op()}})
    assert any("surface method removed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_kept_counterpart_beside_its_replacement_is_additive() -> None:
    base = _spec({"/api/fleet/queue/peek": {"get": _op()}})
    head = _spec({"/api/fleet/queue/peek": {"get": _op(), "post": _op()}})
    assert classify_spec_diff(base, head) == []


def test_non_surface_path_is_ignored() -> None:
    schemas_base = {"Thing": {"type": "object", "properties": {"a": {"type": "string"}}}}
    schemas_head = {"Thing": {"type": "object", "properties": {}}}
    base = _spec({"/api/chunks/{id}": {"get": _op(response_schema=_ref("Thing"))}}, schemas_base)
    head = _spec({}, schemas_head)
    assert classify_spec_diff(base, head) == []


def test_auth_federation_routes_are_on_the_surface() -> None:
    base = _spec({"/api/auth/jwks.json": {"get": _op()}})
    head = _spec({})
    assert any("surface path removed" in v for v in _violation_texts(classify_spec_diff(base, head)))


# --- response-reached schema classes --------------------------------------------------


def _response_widget_specs(base_schema: dict, head_schema: dict) -> tuple[dict, dict]:
    base = _spec({"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {"Widget": base_schema})
    head = _spec({"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {"Widget": head_schema})
    return base, head


def test_response_property_removed_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
        {"type": "object", "properties": {"id": {"type": "string"}}},
    )
    assert any("response property removed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_optional_property_added_is_additive() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}},
        {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
    )
    assert classify_spec_diff(base, head) == []


def test_response_property_type_changed_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"count": {"type": "string"}}},
        {"type": "object", "properties": {"count": {"type": "integer"}}},
    )
    assert any("response property type changed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def _enum_narrowing_specs(*, request: bool, nullable: bool) -> tuple[dict, dict]:
    base_prop: dict = {"anyOf": [{"type": "string"}, {"type": "null"}]} if nullable else {"type": "string"}
    head_prop: dict = {"anyOf": [_ref("Color"), {"type": "null"}]} if nullable else _ref("Color")
    op = (lambda s: _op(request_schema=s)) if request else (lambda s: _op(response_schema=s))
    color = {"type": "string", "enum": ["red", "blue"], "title": "Color"}
    base = _spec(
        {"/api/fleet/widgets": {"post": op(_ref("Widget"))}},
        {"Widget": {"type": "object", "properties": {"color": base_prop}}},
    )
    head = _spec(
        {"/api/fleet/widgets": {"post": op(_ref("Widget"))}},
        {"Widget": {"type": "object", "properties": {"color": head_prop}}, "Color": color},
    )
    return base, head


@pytest.mark.parametrize("nullable", [False, True])
def test_response_string_narrowed_to_enum_component_is_additive(nullable: bool) -> None:
    base, head = _enum_narrowing_specs(request=False, nullable=nullable)
    assert classify_spec_diff(base, head) == []


def test_request_string_narrowed_to_enum_component_is_breaking() -> None:
    base, head = _enum_narrowing_specs(request=True, nullable=False)
    assert any("request property type narrowed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_string_retyped_to_object_component_is_breaking() -> None:
    base, head = _enum_narrowing_specs(request=False, nullable=False)
    head["components"]["schemas"]["Color"] = {"type": "object", "properties": {}}
    assert any("response property type changed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_property_stops_being_required_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": []},
    )
    assert any(
        "response property stopped being required" in v for v in _violation_texts(classify_spec_diff(base, head))
    )


def test_response_property_stops_being_nullable_is_additive() -> None:
    # Safe direction: a runner tolerant of `None` still parses a value that is never `None`.
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"note": {"type": ["string", "null"]}}},
        {"type": "object", "properties": {"note": {"type": "string"}}},
    )
    assert classify_spec_diff(base, head) == []


def test_response_property_newly_becomes_nullable_is_breaking() -> None:
    # Breaking direction: an old runner's non-Optional parse model rejects the new `null`.
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"note": {"type": "string"}}},
        {"type": "object", "properties": {"note": {"type": ["string", "null"]}}},
    )
    assert any("response property newly became nullable" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_property_type_changed_under_anyof_optional_is_breaking() -> None:
    # pydantic's `X | None` compiles to `anyOf: [{...X}, {type: "null"}]`, with no top-level
    # `type` key — the signature must still resolve through the non-null member.
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"count": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
        {"type": "object", "properties": {"count": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}},
    )
    assert any("response property type changed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_property_same_anyof_optional_type_is_additive() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"count": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
        {"type": "object", "properties": {"count": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
    )
    assert classify_spec_diff(base, head) == []


def test_response_enum_gains_value_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready", "done"]}}},
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready", "done", "paused"]}}},
    )
    assert any("response enum gained value" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_enum_loses_value_is_additive() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready", "done", "paused"]}}},
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready", "done"]}}},
    )
    assert classify_spec_diff(base, head) == []


def test_response_union_gains_member_is_breaking() -> None:
    extra_schemas = {
        "A": {"type": "object", "properties": {}},
        "B": {"type": "object", "properties": {}},
    }
    base_schema = {"type": "object", "properties": {"payload": {"anyOf": [_ref("A")]}}}
    head_schema = {"type": "object", "properties": {"payload": {"anyOf": [_ref("A"), _ref("B")]}}}
    base = _spec(
        {"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {"Widget": base_schema, **extra_schemas}
    )
    head = _spec(
        {"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {"Widget": head_schema, **extra_schemas}
    )
    assert any("response union gained member" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_property_added_to_forbid_schema_reached_by_response_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}}},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "extra": {"type": "string"}},
        },
    )
    assert any(
        "property added to a forbid schema a response reaches" in v
        for v in _violation_texts(classify_spec_diff(base, head))
    )


# --- request-reached schema classes -----------------------------------------------------


def _request_widget_specs(base_schema: dict, head_schema: dict) -> tuple[dict, dict]:
    base = _spec({"/api/fleet/widgets": {"post": _op(request_schema=_ref("Widget"))}}, {"Widget": base_schema})
    head = _spec({"/api/fleet/widgets": {"post": _op(request_schema=_ref("Widget"))}}, {"Widget": head_schema})
    return base, head


def test_request_new_required_property_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": []},
        {
            "type": "object",
            "properties": {"id": {"type": "string"}, "kind": {"type": "string"}},
            "required": ["kind"],
        },
    )
    assert any("request property became required" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_optional_property_becomes_required_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": []},
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    )
    assert any("request property became required" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_optional_property_added_is_additive() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": []},
        {"type": "object", "properties": {"id": {"type": "string"}, "note": {"type": "string"}}, "required": []},
    )
    assert classify_spec_diff(base, head) == []


def test_request_property_type_narrowed_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"count": {"type": ["string", "integer"]}}},
        {"type": "object", "properties": {"count": {"type": "string"}}},
    )
    assert any("request property type narrowed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_property_drops_nullability_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"note": {"type": ["string", "null"]}}},
        {"type": "object", "properties": {"note": {"type": "string"}}},
    )
    assert any("request property dropped nullability" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_enum_loses_value_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"kind": {"type": "string", "enum": ["create", "update"]}}},
        {"type": "object", "properties": {"kind": {"type": "string", "enum": ["create"]}}},
    )
    assert any("request enum lost value" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_enum_gains_value_is_additive() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"kind": {"type": "string", "enum": ["create"]}}},
        {"type": "object", "properties": {"kind": {"type": "string", "enum": ["create", "update"]}}},
    )
    assert classify_spec_diff(base, head) == []


def test_property_removed_from_forbid_request_schema_is_breaking() -> None:
    base, head = _request_widget_specs(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
        },
        {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}}},
    )
    assert any(
        "property removed from a forbid request schema" in v for v in _violation_texts(classify_spec_diff(base, head))
    )


def test_property_removed_from_non_forbid_request_schema_is_additive() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}, "note": {"type": "string"}}},
        {"type": "object", "properties": {"id": {"type": "string"}}},
    )
    assert classify_spec_diff(base, head) == []


def test_property_added_to_request_only_forbid_schema_is_additive() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}}},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "extra": {"type": "string"}},
        },
    )
    assert classify_spec_diff(base, head) == []


def test_nested_schema_reachable_by_both_roles_keeps_both_roles() -> None:
    # "Priority" nests inside "Proposal", reached as a response by one op and a request by
    # another — the walk must give "Priority" both roles, not just whichever reaches it first.
    priority_base = {"type": "string", "enum": ["low", "high"]}
    priority_head = {"type": "string", "enum": ["low"]}
    proposal = {"type": "object", "properties": {"priority": _ref("Priority")}}
    paths = {
        "/api/fleet/proposals": {"get": _op(response_schema=_ref("Proposal"))},
        "/api/fleet/proposals/draft": {"post": _op(request_schema=_ref("Proposal"))},
    }
    base = _spec(paths, {"Proposal": proposal, "Priority": priority_base})
    head = _spec(paths, {"Proposal": proposal, "Priority": priority_head})
    assert any("request enum lost value" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_component_schema_deleted_while_still_referenced_is_breaking() -> None:
    base = _spec(
        {"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}},
        {"Widget": {"type": "object", "properties": {}}},
    )
    head = _spec({"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {})
    assert any("component schema deleted" in v for v in _violation_texts(classify_spec_diff(base, head)))


# --- git history walk + ! acknowledgement -----------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    return repo


def _commit_spec(repo: Path, spec: dict, message: str) -> str:
    spec_dir = repo / "openapi"
    spec_dir.mkdir(exist_ok=True)
    (spec_dir / "hub.openapi.json").write_text(json.dumps(spec))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_check_history_fails_on_unacknowledged_break(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _commit_spec(repo, _spec({}), "feat: drop the widgets route")
    assert check_history(base_commit, repo, echo=lambda *_: None) is False


def test_check_history_passes_on_acknowledged_break(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _commit_spec(repo, _spec({}), "feat!: drop the widgets route")
    assert check_history(base_commit, repo, echo=lambda *_: None) is True


def test_check_history_scoped_marker_also_acknowledges() -> None:
    import re

    assert wire_compat._BREAKING_COMMIT_MARKER.match("feat(runner)!: drop a route")
    assert not re.match(wire_compat._BREAKING_COMMIT_MARKER.pattern, "feat(runner): add a route")


def test_check_history_one_acknowledged_break_never_masks_another(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(
        repo, _spec({"/api/fleet/a": {"get": _op()}, "/api/fleet/b": {"get": _op()}}), "chore: base"
    )
    _commit_spec(repo, _spec({"/api/fleet/b": {"get": _op()}}), "feat!: drop a")
    _commit_spec(repo, _spec({}), "feat: drop b")
    assert check_history(base_commit, repo, echo=lambda *_: None) is False


def test_check_history_is_a_noop_with_no_changes_since_baseline(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    assert check_history(base_commit, repo, echo=lambda *_: None) is True


def test_check_history_passes_when_the_step_is_additive(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _commit_spec(
        repo, _spec({"/api/fleet/widgets": {"get": _op()}, "/api/fleet/gadgets": {"get": _op()}}), "feat: add gadgets"
    )
    assert check_history(base_commit, repo, echo=lambda *_: None) is True


def test_check_history_recovers_once_an_unacknowledged_break_is_reverted(tmp_path: Path) -> None:
    # An unacknowledged break landing on master can't be marked `!` after the fact and pushed
    # history can't be rewritten — reverting it makes the net diff additive again.
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _commit_spec(repo, _spec({}), "feat: drop the widgets route")
    _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "feat: restore the widgets route")
    assert check_history(base_commit, repo, echo=lambda *_: None) is True


# --- deployed-mode baseline resolution --------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_resolve_deployed_baseline_reads_gh_run_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout=json.dumps([{"headSha": "deadbeef", "conclusion": "success"}]))

    monkeypatch.setattr(wire_compat.subprocess, "run", fake_run)
    assert wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path) == "deadbeef"
    assert captured["cmd"][:3] == ["gh", "run", "list"]
    assert "push.yml" in captured["cmd"]


def test_resolve_deployed_baseline_skips_unsuccessful_runs_without_a_status_filter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GitHub answers ``--branch`` with ``--status`` from a stale index, so success is picked
    from the newest runs here: an in-progress and a failed run are passed over."""
    captured = {}
    runs = [
        {"headSha": "inflight", "conclusion": ""},
        {"headSha": "broken", "conclusion": "failure"},
        {"headSha": "deployed", "conclusion": "success"},
        {"headSha": "older", "conclusion": "success"},
    ]

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout=json.dumps(runs))

    monkeypatch.setattr(wire_compat.subprocess, "run", fake_run)
    assert wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path) == "deployed"
    assert "--status" not in captured["cmd"]


def test_resolve_deployed_baseline_fails_when_no_successful_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(cmd, **kwargs):
        return _FakeCompleted(stdout=json.dumps([{"headSha": "broken", "conclusion": "failure"}]))

    monkeypatch.setattr(wire_compat.subprocess, "run", fake_run)
    with pytest.raises(WireCompatError):
        wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path)


def test_resolve_deployed_baseline_fails_when_gh_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        return _FakeCompleted(returncode=1, stderr="not authenticated")

    monkeypatch.setattr(wire_compat.subprocess, "run", fake_run)
    with pytest.raises(WireCompatError):
        wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path)


def test_resolve_merge_base_baseline(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({}), "chore: base")
    _git(repo, "branch", "other")
    _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "feat: add widgets")
    assert wire_compat.resolve_merge_base_baseline("other", repo) == base_commit
