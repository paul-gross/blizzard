"""Exercise discovery independently, without requiring migrated refusal declarations."""

import importlib.util
import re
import sys
from pathlib import Path
from shutil import copytree

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.api.me import me
from blizzard.hub.app import create_app_for_export
from tests.support.repo_files import repo_root
from tests.support.runner_producer_fingerprints import FINGERPRINTS
from tests.support.runner_refusal_discovery import (
    boundary_producer_census,
    callable_producers,
    classify_get_receivers,
    coverage_failures,
    direct_exception_dispatch,
    discover,
    discovered_producer_inventory,
    injected_call_fingerprints,
    jwks_wiring,
    match_operations,
    missing_coverage,
    operation_producers,
    producer_file_fingerprints,
    producer_inventory_failures,
    producers,
    proxy_forwarding,
    route_inventory,
    runner_boundary,
)
from tests.support.runner_refusal_inventory import DIRECT_PRODUCERS


def test_injected_call_fingerprints_track_changes_not_formatting(tmp_path: Path) -> None:
    source = tmp_path / "adapter.py"
    source.write_text("def call(channel, path):\n    return channel.get(path)\n")
    first = injected_call_fingerprints(tmp_path)
    source.write_text("def call(channel, path):\n  # formatting is irrelevant\n  return channel.get(path)\n")
    assert injected_call_fingerprints(tmp_path) == first
    source.write_text("def call(channel, path):\n    return channel.post(path)\n")
    assert injected_call_fingerprints(tmp_path) != first
    source.write_text("def call(channel, path):\n    send = channel.get\n    return send(path)\n")
    assert injected_call_fingerprints(tmp_path) != first
    assert injected_call_fingerprints(tmp_path)


# Checked-in candidate boundary, distinct from the discovery executed by the test.
EXPECTED_OPERATIONS = {
    (method, path)
    for method, paths in {
        "GET": """
/api/auth/authorize
/api/auth/jwks.json
/api/fleet/chunk-statuses
/api/fleet/chunks/{chunk_id}
/api/fleet/chunks/{chunk_id}/analytics/counts/agent-types
/api/fleet/chunks/{chunk_id}/analytics/counts/files
/api/fleet/chunks/{chunk_id}/analytics/counts/nodes
/api/fleet/chunks/{chunk_id}/analytics/counts/skills
/api/fleet/chunks/{chunk_id}/analytics/spend/graphs
/api/fleet/chunks/{chunk_id}/analytics/spend/nodes
/api/fleet/chunks/{chunk_id}/envelope
/api/fleet/chunks/{chunk_id}/findings
/api/fleet/chunks/{chunk_id}/findings/{finding_id}
/api/fleet/chunks/{chunk_id}/garden/findings
/api/fleet/chunks/{chunk_id}/garden/proposals/page
/api/fleet/chunks/{chunk_id}/transcript-segments
/api/fleet/chunks/{chunk_id}/work-items
/api/fleet/host
/api/fleet/identity
/api/fleet/questions/{question_id}
/api/fleet/runners/{runner_id}
/api/fleet/runners/{runner_id}/decisions
/api/fleet/scopes
/api/fleet/summary
/api/fleet/system-artifacts
/api/fleet/system-artifacts/{name}
/api/me
""",
        "POST": """
/api/fleet/chunks/{chunk_id}/artifact-check
/api/fleet/chunks/{chunk_id}/completions
/api/fleet/chunks/{chunk_id}/decisions
/api/fleet/chunks/{chunk_id}/hub-advance
/api/fleet/chunks/{chunk_id}/pause
/api/fleet/chunks/{chunk_id}/release
/api/fleet/chunks/{chunk_id}/resume
/api/fleet/chunks/{chunk_id}/route-token
/api/fleet/events
/api/fleet/host
/api/fleet/host/events
/api/fleet/host/runners
/api/fleet/host/runners/{runner_id}/retire
/api/fleet/queue/peek
/api/fleet/routes
/api/fleet/runners
/api/fleet/transcripts
/api/hosts
""",
    }.items()
    for path in paths.split()
}


def test_actual_client_host_admin_archive_and_auth_calls() -> None:
    root = repo_root() / "src/blizzard/runner"
    paths = [
        root / "hub/internal/http_hub.py",
        root / "transcripts/internal/http_archived_transcript_repository.py",
        root / "auth/internal/http_hub_auth_mode.py",
    ]
    reached = {(r.method, r.path) for p in paths for r in discover(p.read_text())[0]}
    assert ("POST", "/api/fleet/routes") in reached
    assert ("GET", "/api/me") in reached
    assert ("POST", "/api/hosts") in reached
    assert ("GET", "/api/fleet/host") in reached
    assert ("GET", "/api/fleet/chunks/{chunk_id}/transcript-segments") in reached
    assert ("GET", "/api/auth/jwks.json") in reached


def test_planted_call_and_dynamic_call_are_visible() -> None:
    reaches, unresolved = discover('def call(self, path):\n self._get("/api/new")\n self._post(path, {})\n')
    assert missing_coverage(reaches, set())
    assert len(unresolved) == 1 and "path" in unresolved[0]


def test_forwarded_transport_and_unknown_caller() -> None:
    source = """
class Client:
 def run(self):
  path = "/api/forwarded"
  self._get(path)
 def _get(self, path):
  self._client.get(path)
"""
    reached, unknown = discover(source)
    assert ("GET", "/api/forwarded") in {(r.method, r.path) for r in reached}
    assert not unknown
    _, unknown = discover(source + "\n def other(self, path):\n  self._get(path)\n")
    assert any("self._client.get(path)" in item for item in unknown)


def test_actual_proxy_and_hub_producers() -> None:
    root = repo_root() / "src/blizzard"
    reaches, _ = discover((root / "runner/api/chunk_detail.py").read_text())
    assert ("POST", "/api/fleet/chunks/{chunk_id}/pause") in {(r.method, r.path) for r in reaches}
    assert producers((root / "hub/api/fleet.py").read_text())


def test_proxy_transport_trace_and_changed_forwarding(tmp_path: Path) -> None:
    runner = repo_root() / "src/blizzard/runner"
    reaches, unknown = proxy_forwarding(runner)
    assert ("POST", "/api/fleet/chunks/{chunk_id}/pause") in reaches
    assert all("forwarding" not in item and "URL construction" not in item for item in unknown)
    assert not unknown
    api = tmp_path / "api"
    api.mkdir()
    source = (runner / "api/hub_proxy.py").read_text()
    old = 'self.forward(\n            "POST",\n            path,'
    assert old in source
    (api / "hub_proxy.py").write_text(source.replace(old, 'self.forward(\n            "GET",\n            path,'))
    _, unknown = proxy_forwarding(tmp_path)
    assert any("HubProxy.post: forwarding" in item for item in unknown)
    (api / "hub_proxy.py").write_text(
        source.replace(
            "url = f\"{self.config.hub_url.rstrip('/')}{path}\"",
            "path = dynamic()\n        url = f\"{self.config.hub_url.rstrip('/')}{path}\"",
        )
    )
    _, unknown = proxy_forwarding(tmp_path)
    assert any("path reassigned" in item for item in unknown)
    (api / "hub_proxy.py").write_text(source)
    (api / "escape.py").write_text("from blizzard.runner.api.hub_proxy import HubProxy\nother = HubProxy\n")
    _, unknown = proxy_forwarding(tmp_path)
    assert any("unsupported HubProxy use" in item for item in unknown)
    (api / "escape.py").write_text(
        "from blizzard.runner.api.hub_proxy import HubProxy\n"
        'def endpoint(request):\n proxy = HubProxy.of(request, "test")\n other(proxy)\n'
    )
    _, unknown = proxy_forwarding(tmp_path)
    assert any("proxy instance escapes" in item for item in unknown)


def test_planted_explicit_producer_is_missing_coverage() -> None:
    found = producers('def handler():\n raise HTTPException(status_code=409, detail="no")\n')
    assert len(missing_coverage(found, set())) == 1


def test_me_direct_producer_inventory_detects_new_refusal() -> None:
    source = (repo_root() / "src/blizzard/hub/api/me.py").read_text()
    expected = DIRECT_PRODUCERS[("GET", "/api/me")]["me"]
    # MeResponse is the successful response model, not an HTTP response constructor.
    direct = {
        expression
        for owner, _, expression in producers(source)
        if owner == "me" and not expression.startswith("MeResponse(")
    }
    assert direct == expected
    planted = source.replace(
        'return problem_response(\n            authentication_required_problem(instance=request.url.path, detail="not authenticated"),',
        'raise HTTPException(status_code=409, detail="planted")\n'
        '        return problem_response(\n            authentication_required_problem(instance=request.url.path, detail="not authenticated"),',
    )
    changed = {
        expression
        for owner, _, expression in producers(planted)
        if owner == "me" and not expression.startswith("MeResponse(")
    }
    assert changed - expected == {"HTTPException(status_code=409, detail='planted')"}


def test_envelope_direct_producer_inventory_includes_missing_graph() -> None:
    source = (repo_root() / "src/blizzard/hub/api/fleet.py").read_text()
    expected = DIRECT_PRODUCERS[("GET", "/api/fleet/chunks/{chunk_id}/envelope")]["get_envelope"]
    direct = {expression for owner, _, expression in producers(source) if owner == "get_envelope"}
    assert direct == expected


def test_boundary_census_detects_new_producer_without_declarations(tmp_path: Path) -> None:
    hub = tmp_path / "src/blizzard/hub"
    api = hub / "api"
    api.mkdir(parents=True)
    (hub / "app.py").write_text("def handler():\n pass\n")
    route = api / "routes.py"
    route.write_text("from fastapi import HTTPException\ndef endpoint():\n raise HTTPException(403)\n")
    baseline = boundary_producer_census(tmp_path)
    route.write_text(
        "from fastapi import HTTPException\ndef endpoint():\n raise HTTPException(403)\n"
        "def helper():\n raise HTTPException(409)\n"
    )
    added = boundary_producer_census(tmp_path) - baseline
    assert len(added) == 1
    assert any(function == "helper" and "409" in expression for _, function, _, expression in added)
    helper = hub / "new_helper.py"
    helper.write_text("from fastapi import HTTPException\ndef refuse():\n raise HTTPException(409)\n")
    assert any(
        path.endswith("hub/new_helper.py") and function == "refuse"
        for path, function, _, _ in boundary_producer_census(tmp_path)
    )
    assert "src/blizzard/hub/new_helper.py" in producer_file_fingerprints(tmp_path)


def test_real_boundary_census_includes_auth_and_handlers() -> None:
    census = boundary_producer_census(repo_root())
    assert any("auth_session.py" in path for path, _, _, _ in census)
    assert any(path.endswith("hub/app.py") for path, _, _, _ in census)


def test_boundary_producer_fingerprints() -> None:
    expected = {
        path: (int(count), digest) for line in FINGERPRINTS.splitlines() for path, count, digest in [line.split()]
    }
    observed = producer_file_fingerprints(repo_root())
    changed = sorted(key for key in observed.keys() | expected.keys() if observed.get(key) != expected.get(key))
    assert not changed, f"HTTP boundary constructor candidates changed in: {changed}"


def test_producer_fingerprints_detect_add_remove_and_status_change(tmp_path: Path) -> None:
    api = tmp_path / "src/blizzard/hub/api"
    api.mkdir(parents=True)
    (api.parent / "app.py").write_text("")
    route = api / "routes.py"
    source = "from fastapi import HTTPException\ndef dependency():\n raise HTTPException(403)\n"
    route.write_text(source)
    baseline = producer_file_fingerprints(tmp_path)
    for changed in (
        source + "def helper():\n raise HTTPException(409)\n",
        "from fastapi import HTTPException\ndef dependency():\n pass\n",
        source.replace("403", "401"),
    ):
        route.write_text(changed)
        assert producer_file_fingerprints(tmp_path) != baseline


def test_operation_producer_inventory_fails_closed() -> None:
    first: tuple[str, str] = ("GET", "/api/first")
    second: tuple[str, str] = ("POST", "/api/second")
    observed: dict[tuple[str, str], set[str]] = {first: {"shared", "planted 409"}, second: {"shared"}}
    inventory: dict[tuple[str, str], set[str]] = {first: {"shared"}, second: {"shared", "planted 409"}}
    failures = producer_inventory_failures(observed, inventory, {second: {"dynamic delegate"}})
    assert failures == [
        "GET /api/first: missing producer: planted 409",
        "POST /api/second: unresolved: dynamic delegate",
    ]
    assert producer_inventory_failures({first: set()}, {}, {}) == ["GET /api/first: missing producer inventory"]


def test_me_reachable_producer_candidates_are_inventoried() -> None:
    # This narrow change detector is not a complete operation inventory: the
    # unresolved calls and FastAPI dependencies still need independent review.
    found, unknown = callable_producers(me)
    assert unknown
    candidates = {re.sub(r":\d+: ", ": ", item) for item in found}
    expected = {
        "blizzard.hub.api.hub_level.hub_services: ProblemHTTPException(problems.hub_store_unavailable_problem)",
        "blizzard.hub.api.me.me: problem_response(authentication_required_problem(instance=request.url.path, detail='not authenticated'))",
        "blizzard.hub.api.me.me: MeResponse(user_id=identity.user_id, username=identity.username, display_name=identity.display_name, role=role, permissions=[*sorted(permissions_for(role, identity.hub_admin))], assignable_roles=[*ASSIGNABLE_ROLES], tenants=tenants, hub_admin=identity.hub_admin)",
        "blizzard.hub.api.problem_rendering.problem_response: JSONResponse(content=content, status_code=_http_status(content, status_code), headers=headers, media_type=PROBLEM_MEDIA_TYPE)",
    }
    assert not candidates - expected, sorted(candidates - expected)


def test_new_reachable_existing_helper_and_unknown_call_fail_inventory() -> None:
    functions = (_planted_dependency, _planted_delegated_refusal, _planted_unknown_call)
    original = [function.__module__ for function in functions]
    try:
        _planted_dependency.__module__ = "blizzard.hub.app"
        _planted_delegated_refusal.__module__ = "blizzard.hub.new_helper"
        _planted_unknown_call.__module__ = "blizzard.hub.app"
        found, unknown = callable_producers(_planted_dependency)
        operation = ("GET", "/planted")
        failures = producer_inventory_failures({operation: found}, {operation: set()}, {operation: unknown})
        assert any("missing producer" in item and "_planted_delegated_refusal" in item for item in failures)
        _, unknown = callable_producers(_planted_unknown_call)
        assert any("missing_callable" in item for item in unknown)
        assert producer_inventory_failures({operation: set()}, {operation: set()}, {operation: unknown})
    finally:
        for function, module in zip(functions, original, strict=True):
            function.__module__ = module


def _planted_unknown_call() -> None:
    missing_callable()  # noqa  # pyright: ignore[reportUndefinedVariable]  # intentional unresolved producer


def test_real_boundary_includes_proxy_and_reports_dynamic_paths() -> None:
    reaches, unknown = runner_boundary(repo_root())
    assert not unknown, "\n".join(unknown)
    assert ("GET", "/api/fleet/system-artifacts") in reaches
    assert ("GET", "/api/fleet/chunks/{lease.chunk_id}/garden/findings") in reaches
    assert ("GET", "/api/auth/jwks.json") in reaches
    assert ("GET", "/api/auth/authorize") in reaches
    assert not any("self._http.get(self._url)" in item for item in unknown)
    assert ("GET", "/api/fleet/system-artifacts/{name}") in reaches
    assert ("GET", "/api/fleet/chunks/{lease.chunk_id}/analytics/counts/files") in reaches
    assert ("GET", "/api/fleet/chunks/{lease.chunk_id}/analytics/spend/graphs") in reaches


def test_real_boundary_rejects_new_http_adapter_and_escaped_transport(tmp_path: Path) -> None:
    runner = tmp_path / "src/blizzard/runner"
    copytree(
        repo_root() / "src/blizzard/runner",
        runner,
        ignore=lambda directory, names: [
            name for name in names if not (Path(directory) / name).is_dir() and not name.endswith(".py")
        ],
    )
    adapter = runner / "hub/internal/http_hub.py"
    original = adapter.read_text()
    adapter.write_text(original + "\ndef planted(self, req):\n    self._client.send(req)\n")
    _, unknown = runner_boundary(tmp_path)
    assert any("unsupported or escaped HTTP client" in item for item in unknown)
    adapter.write_text(original + "\ndef planted(self):\n    return self._client\n")
    _, unknown = runner_boundary(tmp_path)
    assert any("unsupported or escaped HTTP client" in item for item in unknown)
    adapter.write_text(original)
    (runner / "new_hub_adapter.py").write_text('import httpx2\ndef call():\n    return httpx2.get("/api/planted")\n')
    _, unknown = runner_boundary(tmp_path)
    assert any("new_hub_adapter.py" in item and "unreviewed HTTP library import" in item for item in unknown)
    (runner / "new_hub_adapter.py").write_text(
        "class Adapter:\n def __init__(self, client):\n  self.transport = client\n"
        ' def read(self):\n  return self.transport.get("/api/planted")\n'
    )
    _, unknown = runner_boundary(tmp_path)
    assert any("new_hub_adapter.py" in item and "unreviewed injected client field" in item for item in unknown)
    (runner / "new_hub_adapter.py").write_text(
        'def planted(transport):\n alias = transport\n return alias.get("/api/planted")\n'
    )
    _, unknown = runner_boundary(tmp_path)
    assert any("new_hub_adapter.py" in item and "unreviewed injected transport" in item for item in unknown)
    (runner / "new_hub_adapter.py").write_text(
        "class Adapter:\n def __init__(self, transport):\n  self.transport = transport\n"
        ' def read(self):\n  return self.transport.get("/api/planted")\n'
    )
    _, unknown = runner_boundary(tmp_path)
    assert any("new_hub_adapter.py" in item and "unreviewed injected client field" in item for item in unknown)
    (runner / "new_hub_adapter.py").write_text("")
    (runner / "new_hub_adapter.py").write_text(
        "def planted(channel, path):\n alias = channel\n return alias.get(path)\n"
    )
    _, unknown = runner_boundary(tmp_path)
    assert any("new_hub_adapter.py: injected receiver call inventory changed" in item for item in unknown)
    (runner / "new_hub_adapter.py").write_text(
        "class Adapter:\n def __init__(self, channel):\n  self.pipe = channel\n"
        " def read(self, path):\n  return self.pipe.get(path)\n"
    )
    _, unknown = runner_boundary(tmp_path)
    assert any("new_hub_adapter.py: injected receiver call inventory changed" in item for item in unknown)
    (runner / "new_hub_adapter.py").write_text("")
    adapter.write_text(original + '\ndef planted(http_client):\n return http_client.get("/api/new")\n')
    _, unknown = runner_boundary(tmp_path)
    assert any("unreviewed injected transport" in item for item in unknown)
    adapter.write_text(original + "\ndef planted():\n return httpx2.Client()\n")
    _, unknown = runner_boundary(tmp_path)
    assert any("http_hub.py" in item and "HTTP client construction count changed" in item for item in unknown)


def test_renamed_proxy_and_client_receivers_and_unknown_receiver_are_visible() -> None:
    source = """
def call(self, req, unknown):
    channel = HubProxy.of(req, "planted")
    channel.get("/api/proxied")
    self.client.request("POST", "/api/direct")
    unknown.get("/api/unknown")
"""
    reaches, unknown = discover(source)
    assert {(r.method, r.path) for r in reaches} == {("GET", "/api/proxied"), ("POST", "/api/direct")}
    assert any("unknown receiver" in item for item in unknown)


def test_get_receiver_classification_requires_positive_evidence() -> None:
    source = """
def call(request: Request, service):
    mapping = {"key": "value"}
    mapping.get("key")
    request.headers.get("authorization")
    service.get("key")
"""
    assert set(classify_get_receivers(source).values()) == {"local mapping literal", "Request.headers"}
    _, unknown = discover(source)
    assert any("service.get" in item for item in unknown)
    real = (repo_root() / "src/blizzard/runner/api/lease_token.py").read_text()
    assert set(classify_get_receivers(real).values()) == {"Request.headers"}
    assert not discover(real)[1]
    _, unknown = discover('def f(x):\n x.get("a"); x.get("b")\n')
    assert len(unknown) == 2
    _, unknown = discover('def f(request: Request, service):\n request = service\n request.headers.get("x")\n')
    assert len(unknown) == 1


def test_nested_and_literal_mappings_do_not_hide_reassigned_receivers() -> None:
    source = 'def f(flag, service):\n if flag:\n  reasons: dict[str, int] = {"a": 1}\n  reasons.get("a")\n  {"b": 2}.get("b")\n'
    assert not discover(source)[1]
    _, unknown = discover(source.replace('  reasons.get("a")', '  reasons = service\n  reasons.get("a")'))
    assert any("reasons.get" in item for item in unknown)


def test_harness_health_port_requires_its_imported_type() -> None:
    root = repo_root()
    source = (root / "src/blizzard/runner/api/harness_health.py").read_text()
    _, unknown = discover(source, root)
    assert not any("health.get" in item for item in unknown)
    mutated = source.replace("health: IReadHarnessHealth", "health: object")
    _, unknown = discover(mutated, root)
    assert any("health.get" in item for item in unknown)
    mutated = source.replace("health: IReadHarnessHealth", "health: IReadHarnessHealth").replace(
        "result = reported_health", "health = dynamic()\n        result = reported_health"
    )
    _, unknown = discover(mutated, root)
    assert any("health.get" in item for item in unknown)


def test_wired_services_require_the_recognized_receiver() -> None:
    root = repo_root()
    for filename, original, changed in [
        ("selftests.py", "service = RunnerWiring.of(request).selftests()", "service = unknown()"),
        ("ticks.py", "RunnerWiring.of(request).stepper().request()", "other.stepper().request()"),
        ("ticks.py", "RunnerWiring.of(request).host_stepper().request()", "other.host_stepper().request()"),
    ]:
        source = (root / "src/blizzard/runner/api" / filename).read_text()
        _, unknown = discover(source.replace(original, changed), root)
        assert any("unknown receiver" in item for item in unknown)


def test_jwks_data_classification_rejects_mutated_sources() -> None:
    root = repo_root()
    source = (root / "src/blizzard/runner/auth/internal/http_jwks_cache.py").read_text()
    _, unknown = discover(source, root)
    assert not any("resp.json().get" in item or "jwk.get" in item or "self._keys.get" in item for item in unknown)
    for original, replacement, expected in [
        ("resp = self._http.get(self._url)", "resp = other()", "resp.json().get"),
        ('for jwk in resp.json().get("keys", []):', "for jwk in other():", "jwk.get"),
        ("self._keys = keys", "self._keys = other()", "self._keys.get"),
    ]:
        _, unknown = discover(source.replace(original, replacement), root)
        assert any(expected in item for item in unknown)


def test_imported_port_return_mapping_and_unknown_remain_visible(tmp_path: Path) -> None:
    module = tmp_path / "src/blizzard/example"
    module.mkdir(parents=True)
    (module / "port.py").write_text(
        "class Base:\n def lookup(self) -> dict[str, str]: ...\nclass Port(Base):\n def dynamic(self): ...\n"
    )
    source = (
        "from blizzard.example.port import Port\n"
        'def f(port: Port):\n a = port.lookup()\n a.get("x")\n b = port.dynamic()\n b.get("x")\n'
    )
    _, unknown = discover(source, tmp_path)
    assert len(unknown) == 1 and "b.get" in unknown[0]
    (module / "port.py").write_text("class Base:\n def lookup(self): ...\nclass Port(Base):\n def dynamic(self): ...\n")
    _, unresolved_changed = discover(source, tmp_path)
    assert any("a.get" in item for item in unresolved_changed)
    (module / "port.py").write_text(
        "class Base:\n def lookup(self) -> dict[str, str]: ...\nclass Port(Base):\n def dynamic(self): ...\n"
    )
    _, unknown = discover(source.replace("a = port.lookup()", "a = port.lookup()\n a = port.dynamic()"), tmp_path)
    assert any("a.get" in item for item in unknown)
    _, unknown = discover('def f(service):\n value = {}\n value = service\n value.get("x")\n')
    assert len(unknown) == 1


def test_frozen_request_field_classification() -> None:
    source = '@dataclass(frozen=True)\nclass C:\n request: Request\n def f(self):\n  self.request.cookies.get("x")\n'
    assert not discover(source)[1]
    assert discover(source.replace("frozen=True", "frozen=False"))[1]
    _, unknown = discover('def values() -> dict[str, str]:\n return {}\ndef f():\n value = values()\n value.get("x")\n')
    assert not unknown
    _, unknown = discover(
        'def values() -> dict[str, str]:\n return dynamic()\ndef f():\n value = values()\n value.get("x")\n'
    )
    assert len(unknown) == 1


def test_response_aliases_and_plain_text_producers_are_visible() -> None:
    found = producers("""
from starlette.responses import PlainTextResponse as Denied
from fastapi import HTTPException as Refused
def endpoint():
    raise Refused(status_code=403)
    return Denied("no", status_code=403)
""")
    assert len(found) == 2


def test_problem_response_producers_are_independently_detected(tmp_path: Path) -> None:
    api = tmp_path / "src/blizzard/hub/api"
    api.mkdir(parents=True)
    source = api / "routes.py"
    source.write_text(
        "from x import problem_response as refuse\ndef endpoint():\n return refuse(problem(), status_code=403)\n"
    )
    baseline = producer_file_fingerprints(tmp_path)
    assert any("refuse(" in expression for _, _, expression in producers(source.read_text()))
    source.write_text(
        source.read_text() + "def other():\n return problems.problem_response(problem(), status_code=409)\n"
    )
    assert producer_file_fingerprints(tmp_path) != baseline
    assert any("problem_response" in expression for _, _, _, expression in boundary_producer_census(tmp_path))


def test_discovered_operations_and_producers_fail_against_missing_inventory() -> None:
    expected, _ = runner_boundary(repo_root())
    app = create_app_for_export(packaged_flag_registry())
    routes, _ = route_inventory(app)
    matched, missing = match_operations(expected, set(routes))
    assert not missing
    operation_inventory = {operation for operations in matched.values() for operation in operations}
    assert operation_inventory == EXPECTED_OPERATIONS
    planted, _ = discover('def call(self):\n self._get("/api/planted")\n')
    planted_operations = {(reach.method, reach.path) for reach in planted}
    assert any("/api/planted" in failure for failure in coverage_failures(planted_operations, EXPECTED_OPERATIONS))
    baseline = producers("def endpoint():\n raise HTTPException(status_code=403)\n")
    changed = producers(
        "def endpoint():\n raise HTTPException(status_code=403)\n raise HTTPException(status_code=409)\n"
    )
    assert any("409" in failure for failure in coverage_failures(changed, baseline))


def test_jwks_wiring_keeps_dynamic_constructor_visible(tmp_path: Path) -> None:
    directory = tmp_path / "auth/internal"
    directory.mkdir(parents=True)
    (directory / "http_jwks_cache.py").write_text(
        "class JwksCache:\n def __init__(self, client, url):\n  self._url = url\n"
    )
    (tmp_path / "app.py").write_text(
        "from blizzard.runner.auth.internal.http_jwks_cache import JwksCache\n"
        'cache = JwksCache(client, "/api/auth/jwks.json")\n'
        "other = JwksCache(client, dynamic)\n"
    )
    urls, complete = jwks_wiring(tmp_path)
    assert urls == {"/api/auth/jwks.json"}
    assert not complete


def test_operation_boundary_matches_parameters_and_reports_missing() -> None:
    reaches = {("GET", "/api/fleet/chunks/{lease.chunk_id}"), ("POST", "/api/new")}
    operations = {("GET", "/api/fleet/chunks/{chunk_id}")}
    matched, missing = match_operations(reaches, operations)
    assert matched[("GET", "/api/fleet/chunks/{lease.chunk_id}")] == operations
    assert missing == {("POST", "/api/new")}


def test_real_routes_dependencies_and_app_exception_handlers_are_candidates() -> None:
    app = create_app_for_export(packaged_flag_registry())
    routes, unknown = route_inventory(app)
    assert not unknown
    assert ("GET", "/api/me") in routes
    found, unresolved = operation_producers(routes[("GET", "/api/me")], app)
    assert any("refuse_closed_tenant" in item for item in found)
    assert any("auth_session" in item for item in found)
    assert unresolved


def test_actual_operation_inventory_preserves_unresolved_candidates() -> None:
    app = create_app_for_export(packaged_flag_registry())
    operation = ("GET", "/api/me")
    observed, unresolved, missing = discovered_producer_inventory({operation, ("GET", "/api/planted")}, app)
    assert any("auth_session" in producer for producer in observed[operation])
    assert unresolved[operation]
    assert missing == ["unmatched reach: GET /api/planted"]
    assert producer_inventory_failures(observed, {operation: observed[operation]}, unresolved)


def test_actual_boundary_inventory_detects_removed_producer() -> None:
    reaches, outbound_unknown = runner_boundary(repo_root())
    app = create_app_for_export(packaged_flag_registry())
    observed, unresolved, missing = discovered_producer_inventory(reaches, app)
    assert not missing
    assert not outbound_unknown, "\n".join(outbound_unknown)
    assert len(observed) >= 40
    operation = ("GET", "/api/me")
    producer = next(item for item in observed[operation] if "auth_session" in item)
    inventory = {key: set(values) for key, values in observed.items()}
    inventory[operation].remove(producer)
    failures = producer_inventory_failures(observed, inventory, unresolved)
    assert any("GET /api/me: missing producer:" in failure and producer in failure for failure in failures)


def _planted_delegated_refusal() -> None:
    raise HTTPException(status_code=409, detail="delegated")


def _planted_dependency() -> None:
    _planted_delegated_refusal()


def _planted_endpoint(_dependency: None = Depends(_planted_dependency)) -> None:
    pass


async def _planted_handler(request: object, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "app refusal"})


def _plain_endpoint() -> None:
    pass


def test_planted_dependency_delegation_and_app_handler_producers() -> None:
    # Give the test's functions the same module boundary as the hub functions the
    # analyzer intentionally follows. Their globals still contain the helpers.
    functions = (_planted_endpoint, _planted_dependency, _planted_delegated_refusal, _planted_handler)
    original = [function.__module__ for function in functions]
    try:
        for function in functions:
            function.__module__ = "blizzard.hub.app"
        _planted_delegated_refusal.__module__ = "blizzard.hub.new_helper"
        app = FastAPI()
        app.add_api_route("/planted", _planted_endpoint, methods=["GET"])
        app.add_exception_handler(ValueError, _planted_handler)
        routes, _ = route_inventory(app)
        found, _ = operation_producers(routes[("GET", "/planted")], app)
        assert any("_planted_delegated_refusal" in item for item in found)
        assert any("_planted_handler" in item for item in found)
        assert len(missing_coverage(found, set())) >= 2
    finally:
        for function, module in zip(functions, original, strict=True):
            function.__module__ = module


def test_imported_hub_helper_is_traced_from_endpoint(tmp_path: Path) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("from fastapi import HTTPException\ndef refuse():\n raise HTTPException(409)\n")
    endpoint = tmp_path / "endpoint.py"
    endpoint.write_text("from blizzard.hub.planted_helper import refuse\ndef endpoint():\n refuse()\n")
    names = ["blizzard.hub.planted_helper", "blizzard.hub.planted_endpoint"]
    try:
        for name, path in zip(names, (helper, endpoint), strict=True):
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        app = FastAPI()
        app.add_api_route("/planted", sys.modules[names[1]].endpoint, methods=["GET"])
        routes, _ = route_inventory(app)
        found, _ = operation_producers(routes[("GET", "/planted")], app)
        assert any("blizzard.hub.planted_helper.refuse" in item and "409" in item for item in found)
    finally:
        for name in names:
            sys.modules.pop(name, None)


def test_direct_exception_dispatch_records_only_explicit_raises() -> None:
    app = FastAPI()
    app.add_api_route("/planted", _planted_delegated_refusal, methods=["GET"])
    app.add_exception_handler(HTTPException, _planted_handler)
    routes, _ = route_inventory(app)
    evidence = direct_exception_dispatch(routes[("GET", "/planted")], app)
    assert any("_planted_handler" in key for key in evidence)
    assert any("_planted_delegated_refusal" in item for items in evidence.values() for item in items)


def test_router_include_dependency_is_traced() -> None:
    original = [_planted_dependency.__module__, _planted_delegated_refusal.__module__]
    try:
        _planted_dependency.__module__ = "blizzard.hub.app"
        _planted_delegated_refusal.__module__ = "blizzard.hub.app"
        router = APIRouter()
        router.add_api_route("/included", _plain_endpoint, methods=["GET"])
        app = FastAPI()
        app.include_router(router, dependencies=[Depends(_planted_dependency)])
        routes, unknown = route_inventory(app)
        assert not unknown
        route = routes[("GET", "/included")]
        assert any(dep.call is _planted_dependency for dep in route.dependant.dependencies)
        found, _ = operation_producers(route, app)
        assert any("_planted_delegated_refusal" in item for item in found)
    finally:
        _planted_dependency.__module__, _planted_delegated_refusal.__module__ = original
