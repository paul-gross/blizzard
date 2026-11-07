"""Planted delegation paths stay visible without executing inspected code."""

import importlib.util
import sys
from pathlib import Path
from types import FunctionType, ModuleType

import pytest
from fastapi import Depends, FastAPI

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.app import create_app_for_export
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from tests.support.fleet_producer_inventory import REVIEWED as FLEET_REVIEWED
from tests.support.me_producer_inventory import REVIEWED
from tests.support.non_fleet_producer_inventory import REVIEWED as NON_FLEET_REVIEWED
from tests.support.producer_delegation import (
    application_source_digest,
    delegation_candidates,
    evidence_digest,
    operation_evidence,
    producer_inventory_failures,
    source_fingerprint,
)
from tests.support.producer_delegation_baseline import APPLICATION_SOURCE_DIGEST, EVIDENCE
from tests.support.producer_delegation_report import closure_failures, render_report
from tests.support.repo_files import repo_root
from tests.support.runner_refusal_discovery import match_operations, route_inventory, runner_boundary

# Concrete implementation wired for the unresolved /api/me memberships call.
COLLABORATOR_SOURCES = {
    MembershipRepository.memberships_for: "ac8ac16f9db4422d667ca722b332371623cfcb272e7b341691361e6f6603beba",
}


def test_closure_reports_each_operation_and_discovery_unknown_without_baseline_exemptions() -> None:
    evidence: dict[tuple[str, str], tuple[set[str], set[str]]] = {
        ("GET", "/one"): ({"producer"}, {"unresolved call callback", "source unavailable: helper"}),
        ("POST", "/two"): (set(), {"unresolved call callback"}),
    }
    failures = closure_failures(evidence, ["unmatched operation: GET /missing"])
    assert failures == [
        "discovery: unmatched operation: GET /missing",
        "GET /one: source unavailable: helper",
        "GET /one: unresolved call callback",
        "POST /two: unresolved call callback",
    ]
    assert "GET /one: 2 unresolved" in render_report(evidence, [])
    assert not closure_failures({("GET", "/clean"): ({"producer"}, set())}, [])
    assert closure_failures({}, ["unsupported route"])


def test_real_baselined_unknowns_do_not_close() -> None:
    routes, unknown = route_inventory(create_app_for_export(packaged_flag_registry()))
    assert not unknown
    operation = ("GET", "/api/me")
    evidence = operation_evidence(routes[operation])
    assert not _boundary_failures(operation, evidence)
    assert any(
        "GET /api/me:" in failure and "memberships_for" in failure
        for failure in closure_failures({operation: evidence}, [])
    )


def test_reviewed_collaborator_sources() -> None:
    changed = [
        f"{function.__module__}.{function.__qualname__}: {source_fingerprint(function)}"
        for function, expected in COLLABORATOR_SOURCES.items()
        if source_fingerprint(function) != expected
    ]
    assert not changed, "review changed collaborator implementations:\n" + "\n".join(changed)


def test_unresolved_application_implementations_change_detector() -> None:
    actual = application_source_digest(repo_root())
    assert actual == APPLICATION_SOURCE_DIGEST, f"review hub implementation and wiring changes: {actual}"


def test_application_digest_detects_injected_collaborator_and_wiring_changes(tmp_path: Path) -> None:
    hub = tmp_path / "src" / "blizzard" / "hub"
    hub.mkdir(parents=True)
    collaborator = hub / "repository.py"
    collaborator.write_text("class Repository:\n def lookup(self):\n  return []\n")
    wiring = hub / "wiring.py"
    wiring.write_text("def make():\n return Repository()\n")
    baseline = application_source_digest(tmp_path)
    collaborator.write_text("class Repository:\n def lookup(self):\n  raise HTTPException(409)\n")
    assert application_source_digest(tmp_path) != baseline
    collaborator.write_text("class Repository:\n def lookup(self):\n  return []\n")
    wiring.write_text("def make():\n return OtherRepository()\n")
    assert application_source_digest(tmp_path) != baseline
    wiring.write_text("def make():\n return Repository()\n")
    (hub / "new.py").write_text("def refuse():\n raise HTTPException(403)\n")
    assert application_source_digest(tmp_path) != baseline


def test_collaborator_source_mutation_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "collaborator.py"
    path.write_text("def lookup():\n return 1\n")
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_collaborator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = source_fingerprint(module.lookup)
    path.write_text("def lookup():\n return 2\n")
    assert source_fingerprint(module.lookup) != original


def test_transitive_module_attribute_and_dynamic_calls(tmp_path: Path) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("from fastapi import HTTPException\ndef refuse():\n raise HTTPException(409)\n")
    endpoint = tmp_path / "endpoint.py"
    endpoint.write_text(
        "import blizzard.hub.planted_helper as helper\n"
        "def endpoint(service, name):\n helper.refuse()\n service.refuse()\n getattr(service, name)()\n"
    )
    names = ("blizzard.hub.planted_helper", "blizzard.hub.planted_endpoint")
    try:
        modules: list[ModuleType] = []
        for name, path in zip(names, (helper, endpoint), strict=True):
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules.append(module)
        function = vars(modules[1])["endpoint"]
        assert isinstance(function, FunctionType)
        found, unknown = delegation_candidates(function)
        assert any("planted_helper.refuse" in item and "409" in item for item in found)
        assert any("service.refuse" in item for item in unknown)
        assert any("getattr" in item for item in unknown)
    finally:
        for name in names:
            sys.modules.pop(name, None)


def test_definition_expressions_only_execute_for_nested_definitions(tmp_path: Path) -> None:
    path = tmp_path / "definition.py"
    path.write_text(
        "from fastapi import HTTPException\n"
        "def decorator(fn):\n return fn\n"
        "def register():\n return decorator\n"
        "def refuse():\n raise HTTPException(409)\n"
        "@register()\n"
        "def endpoint(value=refuse):\n"
        " @decorator\n def nested(value=refuse()):\n  pass\n"
        " callback()\n"
    )
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_definition", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found, unresolved = delegation_candidates(module.endpoint)
    assert any("refuse" in item and "409" in item for item in found)
    assert any("unresolved call callback" in item for item in unresolved)
    assert not any("unresolved call register" in item or "planted_definition.register" in item for item in unresolved)


def test_local_shadow_and_descriptor_are_not_resolved(tmp_path: Path) -> None:
    path = tmp_path / "endpoint.py"
    path.write_text(
        "def endpoint(helper, obj):\n helper.refuse()\n obj.property()\n callback()\n callback = helper.refuse\n"
    )
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_endpoint", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = vars(module)["endpoint"]
    assert isinstance(function, FunctionType)
    found, unknown = delegation_candidates(function)
    assert not found
    assert all(any(expr in item for item in unknown) for expr in ("helper.refuse", "obj.property", "callback"))


def test_local_alias_and_rebindings(tmp_path: Path) -> None:
    path = tmp_path / "endpoint.py"
    path.write_text(
        "from fastapi import HTTPException\n"
        "def refuse():\n raise HTTPException(409)\n"
        "def endpoint(callback):\n"
        " invoke = refuse\n invoke()\n"
        " rebound = refuse\n rebound = callback\n rebound()\n"
        " conditional()\n"
        " if callback:\n  conditional = refuse\n"
    )
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_alias", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found, unknown = delegation_candidates(module.endpoint)
    assert any("refuse" in item and "409" in item for item in found)
    assert not any("unresolved call invoke" in item for item in unknown)
    assert any("unresolved call rebound" in item for item in unknown)
    assert any("unresolved call conditional" in item for item in unknown)


def test_class_factory_delegation_and_unsafe_variants(tmp_path: Path) -> None:
    path = tmp_path / "factory.py"
    path.write_text(
        "from fastapi import HTTPException\n"
        "class Factory:\n"
        " @classmethod\n def build(cls, value):\n  raise HTTPException(409)\n"
        " @staticmethod\n def static(value):\n  raise HTTPException(410)\n"
        " @property\n def dynamic(self):\n  raise RuntimeError('must not execute')\n"
        "class Child(Factory): pass\n"
        "def direct(value):\n return Child.build(value).demand()\n"
        "def static(value):\n return Factory.static(value)\n"
        "def shadow(Factory):\n return Factory.build(1)\n"
        "def rebound(value):\n Factory.build = value\n return Factory.build(1)\n"
        "def side_effect(callback):\n return Factory.build(callback())\n"
        "def descriptor():\n return Factory.dynamic()\n"
        "def nested():\n return wrap(Factory.build(1))\n"
    )
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_factory", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, status in (("direct", "409"), ("static", "410")):
        found, unknown = delegation_candidates(vars(module)[name])
        assert any("Factory." in item and status in item for item in found)
        assert not any(
            item.endswith(("unresolved call Child.build", "unresolved call Factory.static")) for item in unknown
        )
    for name in ("shadow", "rebound", "side_effect", "descriptor", "nested"):
        found, unknown = delegation_candidates(vars(module)[name])
        assert not found
        assert any("unresolved call Factory." in item for item in unknown)


def test_real_class_factory_is_traversed() -> None:
    from blizzard.hub.api.auth import reject_runner_principal, require_host_principal, require_runner_principal

    found, unknown = delegation_candidates(require_runner_principal)
    assert any("RunnerAuth.demand" in item and "HTTPException" in item for item in found)
    assert not any(item.endswith("unresolved call RunnerAuth.of") for item in unknown)
    assert any("RunnerAuth.of" in item and "unresolved call cls" in item for item in unknown)
    assert any("unresolved call RunnerAuth.of(request, services).demand" in item for item in unknown)
    for function, method in (
        (reject_runner_principal, "RunnerAuth.refuse_runner"),
        (require_host_principal, "HostAuth.demand"),
    ):
        found, unknown = delegation_candidates(function)
        assert any(method in item and "HTTPException" in item for item in found)
        assert any("unresolved call" in item and ".of(request, services)." in item for item in unknown)


def test_auth_factory_candidates_are_additive_and_static(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from blizzard.hub.api.auth import RunnerAuth

    path = tmp_path / "auth_candidate.py"
    path.write_text(
        "from fastapi import HTTPException\n"
        "from blizzard.hub.api.auth import RunnerAuth, HostAuth\n"
        "def planted(self):\n raise HTTPException(418)\n"
        "def endpoint(request, services):\n return RunnerAuth.of(request, services).demand()\n"
        "def shadow(RunnerAuth):\n return RunnerAuth.of(1, 2).demand()\n"
        "def imported():\n from other import RunnerAuth\n return RunnerAuth.of(1, 2).demand()\n"
        "def wrong(request, services):\n return HostAuth.of(request, services).refuse_runner()\n"
    )
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_auth_candidate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(RunnerAuth, "demand", module.planted)
    found, unknown = delegation_candidates(module.endpoint)
    assert any("planted" in item and "418" in item for item in found)
    assert any("unresolved call RunnerAuth.of(request, services).demand" in item for item in unknown)
    assert producer_inventory_failures(("GET", "/planted"), found, {("GET", "/planted"): set()})
    for function in (module.shadow, module.imported, module.wrong):
        found, unknown = delegation_candidates(function)
        assert not any("418" in item for item in found)
        assert any("unresolved call" in item and ".of(" in item for item in unknown)
    monkeypatch.setattr(RunnerAuth, "demand", property(lambda self: 1))
    found, unknown = delegation_candidates(module.endpoint)
    assert not any("418" in item for item in found)
    assert any("unresolved call RunnerAuth.of(request, services).demand" in item for item in unknown)


def test_bound_dependency_traverses_method_and_keeps_instance_calls_unknown(tmp_path: Path) -> None:
    path = tmp_path / "dependency.py"
    path.write_text(
        "from fastapi import HTTPException\n"
        "class Dependency:\n"
        " def check(self):\n"
        "  self.delegate()\n"
        "  raise HTTPException(409)\n"
    )
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_dependency", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dependency = module.Dependency()
    app = FastAPI()

    @app.get("/planted", dependencies=[Depends(dependency.check)])
    def endpoint() -> None:
        pass

    routes, unknown = route_inventory(app)
    assert not unknown
    found, unresolved = operation_evidence(routes[("GET", "/planted")])
    assert any("Dependency.check" in item and "409" in item for item in found)
    assert any("unresolved call self.delegate" in item for item in unresolved)
    assert not any("unsupported dependency" in item for item in unresolved)


def test_bound_dependency_resolves_plain_inherited_method_without_descriptors(tmp_path: Path) -> None:
    path = tmp_path / "dependency.py"
    path.write_text(
        "from fastapi import HTTPException\n"
        "class Base:\n"
        " def delegate(self):\n  raise HTTPException(409)\n"
        "class Dependency(Base):\n"
        " def check(self):\n  self.delegate()\n"
        " def check_dynamic(self):\n  self.dynamic()\n"
        " @property\n def dynamic(self):\n  raise RuntimeError('must not execute')\n"
    )
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_bound", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dependency = module.Dependency()
    found, unresolved = delegation_candidates(dependency.check.__func__, dependency)
    assert any("Base.delegate" in item and "409" in item for item in found)
    assert not any("unresolved call self.delegate" in item for item in unresolved)
    _, unresolved = delegation_candidates(dependency.check_dynamic.__func__, dependency)
    assert any("unresolved call self.dynamic" in item for item in unresolved)
    dependency.delegate = lambda: None
    found, unresolved = delegation_candidates(dependency.check.__func__, dependency)
    assert not found
    assert any("unresolved call self.delegate" in item for item in unresolved)


def test_bound_delegate_literal_arguments_and_unsafe_arguments(tmp_path: Path) -> None:
    path = tmp_path / "dependency.py"
    path.write_text(
        "from fastapi import HTTPException\n"
        "class Dependency:\n"
        " def delegate(self, *args, **kwargs):\n  raise HTTPException(409)\n"
        " def safe(self):\n  pass\n  return self.delegate(1, reason='x')\n"
        " def ordinary(self, value):\n  local = value\n  return self.delegate(local, reason=value)\n"
        " def unsafe(self, callback):\n  return self.delegate(callback())\n"
        " def preceding_call(self, callback):\n  callback()\n  return self.delegate(1)\n"
        " def rebound(self, value):\n  self = value\n  return self.delegate(value)\n"
        " def star(self, args):\n  return self.delegate(*args)\n"
    )
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_literals", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dependency = module.Dependency()
    found, unresolved = delegation_candidates(dependency.safe.__func__, dependency)
    assert any("Dependency.delegate" in item and "409" in item for item in found)
    assert not any("unresolved call self.delegate" in item for item in unresolved)
    found, unresolved = delegation_candidates(dependency.ordinary.__func__, dependency)
    assert any("Dependency.delegate" in item and "409" in item for item in found)
    assert not any("unresolved call self.delegate" in item for item in unresolved)
    found, unresolved = delegation_candidates(dependency.unsafe.__func__, dependency)
    assert not found
    assert any("unresolved call self.delegate" in item for item in unresolved)
    assert any("unresolved call callback" in item for item in unresolved)
    for method in (dependency.preceding_call, dependency.rebound, dependency.star):
        found, unresolved = delegation_candidates(method.__func__, dependency)
        assert not found
        assert any("unresolved call self.delegate" in item for item in unresolved)


def test_me_boundary_keeps_reviewed_unknowns_visible() -> None:
    routes, unknown = route_inventory(create_app_for_export(packaged_flag_registry()))
    assert not unknown
    found, unresolved = operation_evidence(routes[("GET", "/api/me")])
    assert any("authentication_required_problem" in item for item in found)
    assert any("services.memberships.memberships_for" in item for item in unresolved)
    assert any("request.headers.get" in item for item in unresolved)
    assert not producer_inventory_failures(("GET", "/api/me"), found, REVIEWED)


def test_source_wired_adapter_is_traversed_without_claiming_exhaustive_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "wiring.py"
    path.write_text(
        "from fastapi import HTTPException\n"
        "class Adapter:\n def memberships_for(self, user):\n  raise HTTPException(409)\n"
        "class HubServices:\n def __init__(self, **kwargs): pass\n"
        "def compose():\n"
        " adapter = Adapter()\n return HubServices(memberships=adapter)\n"
        "def endpoint(services, user):\n return services.memberships.memberships_for(user)\n"
    )
    spec = importlib.util.spec_from_file_location("blizzard.hub.planted_wiring", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr("tests.support.producer_delegation.composition", module)
    found, unresolved = delegation_candidates(module.endpoint)
    assert any("Adapter.memberships_for" in item and "409" in item for item in found)
    assert any("unresolved call services.memberships.memberships_for" in item for item in unresolved)
    failures = producer_inventory_failures(("GET", "/planted"), found, {("GET", "/planted"): set()})
    assert any("Adapter.memberships_for" in item for item in failures)

    def override(self: object, user: object) -> None:
        pass

    module.Adapter.memberships_for = override
    found, unresolved = delegation_candidates(module.endpoint)
    assert any("unresolved call services.memberships.memberships_for" in item for item in unresolved)
    assert not any("409" in item for item in found)


def _boundary_failures(operation: tuple[str, str], evidence: tuple[set[str], set[str]]) -> list[str]:
    baseline = {
        (method, path): (found, unknown) for method, path, found, unknown in map(str.split, EVIDENCE.splitlines())
    }
    if operation not in baseline:
        return [f"{operation}: missing delegation evidence baseline"]
    failures: list[str] = []
    for label, values, expected in zip(("producers", "unresolved"), evidence, baseline[operation], strict=True):
        if evidence_digest(values) != expected:
            failures.append(
                f"{operation} {label} changed ({evidence_digest(values)}); inspect each candidate:\n"
                + "\n".join(sorted(values))
            )
    return failures


def _me_endpoint() -> FunctionType:
    routes, unknown = route_inventory(create_app_for_export(packaged_flag_registry()))
    assert not unknown
    endpoint = routes[("GET", "/api/me")].endpoint
    assert isinstance(endpoint, FunctionType)
    return endpoint


def test_real_operation_delegation_change_detector() -> None:
    reaches, unknown = runner_boundary(repo_root())
    routes, route_unknown = route_inventory(create_app_for_export(packaged_flag_registry()))
    matched, missing = match_operations(reaches, set(routes))
    assert not unknown and not route_unknown and not missing, (unknown, route_unknown, missing)
    operations = {op for group in matched.values() for op in group}
    baseline = {(method, path) for method, path, _, _ in map(str.split, EVIDENCE.splitlines())}
    assert operations == baseline, f"missing={operations - baseline}, stale={baseline - operations}"
    failures = [
        failure for op in sorted(operations) for failure in _boundary_failures(op, operation_evidence(routes[op]))
    ]
    assert not failures, "\n".join(failures)


def test_runner_fleet_producer_inventory() -> None:
    routes, unknown = route_inventory(create_app_for_export(packaged_flag_registry()))
    assert not unknown
    reaches, unknown = runner_boundary(repo_root())
    matched, missing = match_operations(reaches, set(routes))
    assert not unknown and not missing
    fleet = {op for group in matched.values() for op in group if op[1].startswith("/api/fleet/")}
    assert set(FLEET_REVIEWED) == fleet, (fleet - set(FLEET_REVIEWED), set(FLEET_REVIEWED) - fleet)
    failures = [
        failure
        for op in sorted(FLEET_REVIEWED)
        for failure in producer_inventory_failures(op, operation_evidence(routes[op])[0], FLEET_REVIEWED)
    ]
    assert not failures, "\n".join(failures)


def test_runner_non_fleet_producer_inventory() -> None:
    routes, unknown = route_inventory(create_app_for_export(packaged_flag_registry()))
    assert not unknown
    reaches, unknown = runner_boundary(repo_root())
    matched, missing = match_operations(reaches, set(routes))
    assert not unknown and not missing
    operations = {op for group in matched.values() for op in group if not op[1].startswith("/api/fleet/")}
    assert set(NON_FLEET_REVIEWED) == operations, (
        operations - set(NON_FLEET_REVIEWED),
        set(NON_FLEET_REVIEWED) - operations,
    )
    failures = [
        failure
        for op in sorted(operations)
        for failure in producer_inventory_failures(op, operation_evidence(routes[op])[0], NON_FLEET_REVIEWED)
    ]
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("operation", sorted(NON_FLEET_REVIEWED))
def test_non_fleet_inventory_rejects_unreviewed_producer(operation: tuple[str, str]) -> None:
    routes, unknown = route_inventory(create_app_for_export(packaged_flag_registry()))
    assert not unknown
    found, _ = operation_evidence(routes[operation])
    found.add("blizzard.hub.planted.refuse:1: HTTPException(status_code=409)")
    other = next(op for op in NON_FLEET_REVIEWED if op != operation)
    inventory = {
        **NON_FLEET_REVIEWED,
        other: NON_FLEET_REVIEWED[other] | {("blizzard.hub.planted.refuse", "HTTPException(status_code=409)")},
    }
    failures = producer_inventory_failures(operation, found, inventory)
    assert any("planted.refuse" in failure and "409" in failure for failure in failures)


@pytest.mark.parametrize(
    "operation",
    [
        ("POST", "/api/fleet/routes"),
        ("POST", "/api/fleet/host"),
        ("POST", "/api/fleet/chunks/{chunk_id}/release"),
        ("GET", "/api/fleet/scopes"),
    ],
)
def test_claim_and_host_detect_transitive_producer(
    operation: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("from fastapi import HTTPException\ndef refuse():\n raise HTTPException(409)\n")
    delegate = tmp_path / "delegate.py"
    delegate.write_text("import blizzard.hub.planted_helper as helper\ndef call(*args, **kwargs):\n helper.refuse()\n")
    modules: list[ModuleType] = []
    for name, path in (("blizzard.hub.planted_helper", helper), ("blizzard.hub.planted_delegate", delegate)):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    from blizzard.hub.api.problem_rendering import problem_response

    for module in list(sys.modules.values()):
        if getattr(module, "problem_response", None) is problem_response:
            monkeypatch.setattr(module, "problem_response", modules[1].call)
    routes, _ = route_inventory(create_app_for_export(packaged_flag_registry()))
    found, unresolved = operation_evidence(routes[operation])
    failures = producer_inventory_failures(operation, found, FLEET_REVIEWED)
    assert any("planted_helper.refuse" in failure and "409" in failure for failure in failures)
    assert unresolved


def test_real_operation_detects_new_transitive_module_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("from fastapi import HTTPException\ndef refuse():\n raise HTTPException(409)\n")
    delegate = tmp_path / "delegate.py"
    delegate.write_text("import blizzard.hub.planted_helper as helper\ndef call():\n helper.refuse()\n")
    names = ("blizzard.hub.planted_helper", "blizzard.hub.planted_delegate")
    modules: list[ModuleType] = []
    for name, path in zip(names, (helper, delegate), strict=True):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    endpoint = _me_endpoint()
    monkeypatch.setitem(endpoint.__globals__, "hub_services", vars(modules[1])["call"])
    routes, _ = route_inventory(create_app_for_export(packaged_flag_registry()))
    failures = _boundary_failures(("GET", "/api/me"), operation_evidence(routes[("GET", "/api/me")]))
    assert any("planted_helper.refuse" in failure and "409" in failure for failure in failures)
    found, unresolved = operation_evidence(routes[("GET", "/api/me")])
    planted = next(item for item in found if "planted_helper.refuse" in item)
    owner, expression = planted.split(":", 1)
    expression = expression.split(": ", 1)[1]
    inventory = {**REVIEWED, ("GET", "/other"): {(owner, expression)}}
    failures = producer_inventory_failures(("GET", "/api/me"), found, inventory)
    assert any("planted_helper.refuse" in failure and "409" in failure for failure in failures)
    assert unresolved
