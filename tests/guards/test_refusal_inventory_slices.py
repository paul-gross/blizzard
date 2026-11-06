"""Check source-reviewed inventory evidence against hub source and effective routes.

This is not a complete reachability proof: a listed helper's applicability and
unlisted dynamic calls still require independent review.
"""

import ast
import importlib
import inspect
import textwrap
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.dependencies.utils import get_dependant

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.api import auth, problems
from blizzard.hub.api import chunks as chunks_api
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.app import create_app_for_export
from tests.support import refusal_inventory_auth_host, refusal_inventory_claims, refusal_inventory_delegated
from tests.support.repo_files import repo_root
from tests.support.runner_dependency_inventory import inventory as dependency_inventory
from tests.support.runner_refusal_discovery import match_operations, producers, route_inventory, runner_boundary


def _normalized(expression: str) -> str:
    return ast.dump(ast.parse(expression, mode="eval"), include_attributes=False)


def _owner_candidates(root: Path, owner: str) -> dict[str, set[str]]:
    """Resolve an inventory owner to source constructors, retaining module identity."""
    parts = owner.split(".")
    hub = root / "src/blizzard/hub"
    paths = [hub / "app.py", *(hub / "api").glob("*.py")]
    if len(parts) > 1 and (hub / "api" / f"{parts[0]}.py").exists():
        paths = [hub / "api" / f"{parts.pop(0)}.py"]
    elif parts[0] == "app":
        parts.pop(0)
        paths = [hub / "app.py"]
    found: dict[str, set[str]] = {}
    for path in paths:
        tree = ast.parse(path.read_text())
        if len(parts) == 2:
            scopes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == parts[0]]
            definitions = [
                n for scope in scopes for n in scope.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
        else:
            definitions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        for definition in definitions:
            if definition.name != parts[-1]:
                continue
            for function, _, expression in producers(ast.unparse(definition)):
                if function == definition.name:
                    found.setdefault(str(path.relative_to(root)), set()).add(_normalized(expression))
    return found


def _evidence_failures(root: Path, inventory: dict[tuple[str, str], dict[str, set[str]]]) -> list[str]:
    failures: list[str] = []
    for operation, owners in inventory.items():
        for owner, expressions in owners.items():
            candidates = _owner_candidates(root, owner)
            matches = [path for path, found in candidates.items() if {_normalized(x) for x in expressions} <= found]
            if len(matches) != 1:
                failures.append(f"{operation}: {owner}: source evidence missing or ambiguous: {matches}")
    return failures


def _owner_additions(root: Path, owner: str, expressions: set[str]) -> set[str]:
    candidates = _owner_candidates(root, owner)
    expected = {_normalized(expression) for expression in expressions}
    matching = [found for found in candidates.values() if expected <= found]
    if len(matching) != 1:
        return set()
    return matching[0] - expected


def _direct_mismatches(endpoint: Any, owners: dict[str, set[str]]) -> set[str]:
    """Check constructors on the effective endpoint, not other operations' owners."""
    source = textwrap.dedent(inspect.getsource(endpoint))
    name = endpoint.__name__
    actual = {
        _normalized(expression)
        for function, _, expression in producers(source)
        if function == name
        and expression.startswith(
            ("HTTPException(", "ProblemHTTPException(", "JSONResponse(", "PlainTextResponse(", "problem_response(")
        )
    }
    expected = {
        _normalized(expression)
        for owner, expressions in owners.items()
        if owner.split(".")[-1] == name
        for expression in expressions
    }
    return actual - expected


def _direct_helper_calls(endpoint: Any) -> set[str]:
    """Resolve direct global/module calls; typed FleetRequest methods are explicit."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = None
        if isinstance(node.func, ast.Name):
            target = endpoint.__globals__.get(node.func.id)
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            base = endpoint.__globals__.get(node.func.value.id)
            if inspect.ismodule(base):
                target = getattr(base, node.func.attr, None)
            elif node.func.value.id == "fleet" and "FleetRequest" in inspect.getsource(endpoint):
                cls = endpoint.__globals__.get("FleetRequest")
                target = getattr(cls, node.func.attr, None)
        if inspect.isfunction(target):
            found.add(f"{target.__module__.split('.')[-1]}.{target.__qualname__}")
    return found


def _unlisted_known_helpers(endpoint: Any, known: set[str], covered: set[str]) -> set[str]:
    pending = [endpoint]
    reached: set[str] = set()
    while pending:
        current = pending.pop()
        for name in (_direct_helper_calls(current) & known) - reached:
            reached.add(name)
            module, *parts = name.split(".")
            target: Any = importlib.import_module(f"blizzard.hub.api.{module}")
            for part in parts:
                target = getattr(target, part)
            if inspect.isfunction(target):
                pending.append(target)
    return reached - covered


def _dependency_names(route: Any) -> set[str]:
    pending = list(route.dependant.dependencies)
    names: set[str] = set()
    while pending:
        item = pending.pop()
        call = item.call
        if inspect.isfunction(call) or inspect.ismethod(call):
            names.add(f"{call.__module__}.{call.__qualname__}")
        else:
            names.add(f"unsupported dependency: {type(call).__name__}")
        pending.extend(item.dependencies)
    return names


def test_fleet_summary_dependency_surface() -> None:
    app = create_app_for_export(packaged_flag_registry())
    routes, _ = route_inventory(app)
    observed = _dependency_names(routes[("GET", "/api/fleet/summary")])
    expected = dependency_inventory()[("GET", "/api/fleet/summary")]
    assert observed == expected
    route = routes[("GET", "/api/fleet/summary")]
    route.dependant.dependencies[0].dependencies.append(
        get_dependant(path=route.path, call=auth.require_host_principal)
    )
    assert _dependency_names(route) - expected == {"blizzard.hub.api.auth.require_host_principal"}


def test_all_runner_operations_have_reviewed_effective_dependencies() -> None:
    reaches, outbound_unknown = runner_boundary(repo_root())
    assert not outbound_unknown, "\n".join(outbound_unknown)
    app = create_app_for_export(packaged_flag_registry())
    routes, route_unknown = route_inventory(app)
    assert not route_unknown
    matched, missing = match_operations(reaches, set(routes))
    assert not missing
    actual = {operation for operations in matched.values() for operation in operations}
    expected = dependency_inventory()
    assert actual == set(expected), (
        f"dependency operations missing={actual - set(expected)}, stale={set(expected) - actual}"
    )
    for operation in sorted(actual):
        observed = _dependency_names(routes[operation])
        assert not any(name.startswith("unsupported dependency:") for name in observed), (operation, observed)
        assert observed == expected[operation], (
            f"{operation}: added={observed - expected[operation]}, removed={expected[operation] - observed}"
        )


def test_inventory_covers_discovered_operation_boundary() -> None:
    reaches, unknown = runner_boundary(repo_root())
    assert not unknown, "\n".join(unknown)
    operations = (
        set(refusal_inventory_claims.DIRECT_PRODUCERS)
        | set(refusal_inventory_auth_host.DIRECT_PRODUCERS)
        | set(refusal_inventory_delegated.DIRECT_PRODUCERS)
    )
    _, missing = match_operations(reaches, operations)
    assert not missing, f"runner reaches without inventory: {sorted(missing)}"


def test_delegated_owner_is_called_by_its_operation() -> None:
    app = create_app_for_export(packaged_flag_registry())
    routes, unknown = route_inventory(app)
    assert not unknown
    inventory = refusal_inventory_delegated.DIRECT_PRODUCERS
    known_owners = {owner for owners in inventory.values() for owner in owners}
    matched, missing = match_operations(set(inventory), set(routes))
    assert not missing
    for operation, route_keys in matched.items():
        for route_key in route_keys:
            endpoint = routes[route_key].endpoint
            direct = f"{endpoint.__module__.split('.')[-1]}.{endpoint.__qualname__}"
            calls = _direct_helper_calls(endpoint)
            unlisted = _unlisted_known_helpers(endpoint, known_owners, set(inventory[operation]))
            assert not unlisted, f"{operation}: newly reachable inventoried producers: {unlisted}"
            for owner in inventory[operation]:
                assert owner == direct or owner in calls, f"{operation}: no direct evidence for {owner}"


def test_reviewed_inventory_sources_and_routes() -> None:
    app = create_app_for_export(packaged_flag_registry())
    routes, unknown = route_inventory(app)
    assert not unknown
    inventories = [
        refusal_inventory_claims.DIRECT_PRODUCERS,
        refusal_inventory_auth_host.DIRECT_PRODUCERS,
        refusal_inventory_delegated.DIRECT_PRODUCERS,
    ]
    for inventory in inventories:
        matched, missing = match_operations(set(inventory), set(routes))
        assert not missing, missing
        failures = _evidence_failures(repo_root(), inventory)
        assert not failures, "\n".join(failures)
        for operation, route_keys in matched.items():
            for route_key in route_keys:
                mismatch = _direct_mismatches(routes[route_key].endpoint, inventory[operation])
                assert not mismatch, f"{operation}: unlisted endpoint constructors: {mismatch}"


def test_inventory_detects_changed_and_missing_source_constructor(tmp_path: Path) -> None:
    api = tmp_path / "src/blizzard/hub/api"
    api.mkdir(parents=True)
    (api.parent / "app.py").write_text("")
    source = api / "sample.py"
    inventory = {("POST", "/sample"): {"sample.endpoint": {"HTTPException(status_code=409)"}}}
    source.write_text("def endpoint():\n raise HTTPException(status_code=409)\n")
    assert not _evidence_failures(tmp_path, inventory)
    source.write_text("def endpoint():\n raise HTTPException(status_code=403)\n")
    assert _evidence_failures(tmp_path, inventory)


def test_shared_dependency_and_handler_evidence() -> None:
    operation = ("GET", "/api/fleet/summary")
    failures = _evidence_failures(repo_root(), {operation: refusal_inventory_delegated.SHARED_PRODUCERS})
    assert not failures, "\n".join(failures)
    app = create_app_for_export(packaged_flag_registry())
    registered = {handler.__name__ for handler in app.exception_handlers.values()}
    assert {"_refuse_retired_runner", "refuse_closed_tenant", "_log_unhandled_request_error"} <= registered


def test_http_problem_handler_source_and_registration() -> None:
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app = create_app_for_export(packaged_flag_registry())
    handler = app.exception_handlers[StarletteHTTPException]
    assert handler.__module__ == "blizzard.hub.api.problem_exceptions"
    assert handler.__name__ == "problem_http_exception"
    source = textwrap.dedent(inspect.getsource(handler))
    found = {expression for _, _, expression in producers(source)}
    assert "declared_problem_response(request, exc.problem(instance=request.url.path), headers=exc.headers)" in found
    planted = source.replace(
        "    return await http_exception_handler(request, exc)",
        "    problem_response(problem, status_code=418)\n    return await http_exception_handler(request, exc)",
    )
    assert len(producers(planted) - producers(source)) == 1


def _raised_problem_factories(source: str) -> set[str]:
    """The factory each ``ProblemHTTPException`` names, unwrapped from ``partial``."""
    factories: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and ast.unparse(node.func) == "ProblemHTTPException" and node.args):
            continue
        factory = node.args[0]
        if isinstance(factory, ast.Call) and ast.unparse(factory.func) == "partial" and factory.args:
            factory = factory.args[0]
        factories.add(ast.unparse(factory))
    return factories


_RAISED_FACTORIES = {
    "problem",
    *(
        f"problems.{name}_problem"
        for name in (
            "authentication_required",
            "authentication_revoked",
            "chunk_disappeared",
            "chunk_not_found",
            "chunk_not_pausable",
            "finding_not_in_chunk",
            "hub_store_unavailable",
            "lease_ownership",
            "login_disabled",
            "login_provider_unknown",
            "login_throttled",
            "machine_credential_forbidden",
            "malformed_cursor",
            "no_answered_proposal",
            "no_current_node",
            "no_run_context",
            "permission_required",
            "pinned_graph_missing",
            "question_not_found",
            "runner_credential_forbidden",
            "runner_not_found",
            "runner_not_registered",
            "runner_ownership",
            "tenant_credential_mismatch",
            "tenant_not_found",
            "tenant_required",
            "unknown_query_project",
        )
    ),
}


def test_raised_problem_factory_inventory_detects_changes() -> None:
    hub = repo_root() / "src/blizzard/hub"
    sources = [path.read_text() for path in sorted(hub.rglob("*.py"))]
    assert set().union(*map(_raised_problem_factories, sources)) == _RAISED_FACTORIES
    planted = "def planted():\n raise ProblemHTTPException(partial(problems.planted_problem, 1))\n"
    assert _raised_problem_factories(planted) == {"problems.planted_problem"}


def test_claim_dependency_and_handler_evidence() -> None:
    operation = ("POST", "/api/fleet/routes")
    for owners in (refusal_inventory_claims.DEPENDENCY_PRODUCERS, refusal_inventory_claims.EXCEPTION_HANDLERS):
        failures = _evidence_failures(repo_root(), {operation: owners})
        assert not failures, "\n".join(failures)
    app = create_app_for_export(packaged_flag_registry())
    assert set(refusal_inventory_claims.EXCEPTION_HANDLERS) <= {
        handler.__name__ for handler in app.exception_handlers.values()
    }
    routes, _ = route_inventory(app)
    for path, gate in (
        ("/api/fleet/routes", "require_runner_principal_even_if_retired"),
        ("/api/fleet/chunks/{chunk_id}/release", "require_runner_principal"),
    ):
        pending = [routes[("POST", path)].dependant]
        calls: set[str] = set()
        while pending:
            current = pending.pop()
            if current.call is not None:
                calls.add(getattr(current.call, "__name__", ""))
            pending.extend(current.dependencies)
        assert gate in calls
        assert "tenant_bound" in calls


def test_migrated_helpers_and_retired_handlers_remain_covered() -> None:
    app = create_app_for_export(packaged_flag_registry())
    routes, unknown = route_inventory(app)
    assert not unknown
    for operation, helper in (
        (("GET", "/api/fleet/host"), "fleet_host._unresolved"),
        (("POST", "/api/fleet/host"), "fleet_host._unresolved"),
        (("POST", "/api/fleet/host/events"), "fleet_host._unresolved"),
        (("POST", "/api/fleet/host/runners"), "fleet_host._unresolved"),
        (("GET", "/api/auth/authorize"), "idp._unknown_client"),
    ):
        assert helper in _direct_helper_calls(routes[operation].endpoint)
        assert helper in refusal_inventory_auth_host.DIRECT_PRODUCERS[operation]
    for owner in ("app._refuse_retired_runner", "app._refuse_retired_host"):
        expressions = refusal_inventory_auth_host.DIRECT_PRODUCERS[("GET", "/api/auth/authorize")][owner]
        assert not _owner_additions(repo_root(), owner, expressions)
        assert len(expressions) == 2


def test_class_qualified_owner_does_not_borrow_another_class(tmp_path: Path) -> None:
    api = tmp_path / "src/blizzard/hub/api"
    api.mkdir(parents=True)
    (api.parent / "app.py").write_text("")
    (api / "auth.py").write_text(
        "class RunnerAuth:\n def demand(self):\n  raise HTTPException(401)\n"
        "class HostAuth:\n def demand(self):\n  raise HTTPException(403)\n"
    )
    assert _evidence_failures(tmp_path, {("GET", "/"): {"auth.HostAuth.demand": {"HTTPException(401)"}}})
    assert not _evidence_failures(tmp_path, {("GET", "/"): {"auth.RunnerAuth.demand": {"HTTPException(401)"}}})


def test_reviewed_delegated_helpers_have_no_unlisted_constructors() -> None:
    # Unlike dependencies with branch-specific inventories, these helpers'
    # complete constructor sets apply to every operation that names them.
    owners: dict[str, set[str]] = {}
    for operation in refusal_inventory_delegated.DIRECT_PRODUCERS.values():
        for owner, expressions in operation.items():
            owners.setdefault(owner, set()).update(expressions)
    for owner, expressions in owners.items():
        additions = _owner_additions(repo_root(), owner, expressions)
        assert not additions, f"{owner}: unlisted constructors: {additions}"


def test_reviewed_auth_dependencies_have_no_unlisted_constructors() -> None:
    shared = refusal_inventory_delegated.SHARED_PRODUCERS
    for owner in ("auth.RunnerAuth.demand", "auth_session._no_such_tenant"):
        additions = _owner_additions(repo_root(), owner, shared[owner])
        assert not additions, f"{owner}: unlisted constructors: {additions}"


def test_new_constructor_in_reviewed_helper_is_detected(tmp_path: Path) -> None:
    api = tmp_path / "src/blizzard/hub/api"
    api.mkdir(parents=True)
    (api.parent / "app.py").write_text("")
    source = api / "sample.py"
    source.write_text("def helper():\n raise HTTPException(403)\n")
    expected = {"HTTPException(403)"}
    assert not _owner_additions(tmp_path, "sample.helper", expected)
    source.write_text("def helper():\n raise HTTPException(403)\n raise HTTPException(409)\n")
    assert _owner_additions(tmp_path, "sample.helper", expected)


def _planted_endpoint() -> None:
    raise HTTPException(status_code=409)


def _planted_problem_endpoint() -> None:
    raise ProblemHTTPException(partial(problems.authentication_required_problem, detail="denied"))


def _planted_delegated() -> None:
    raise HTTPException(status_code=403)


def _planted_calls_delegate() -> None:
    _planted_delegated()


def _planted_known_helper(chunk_id: str, services: Any) -> Any:
    return chunks_api.get_chunk(chunk_id, services)


def test_unlisted_direct_producer_cannot_borrow_another_operation() -> None:
    assert _direct_mismatches(_planted_endpoint, {})
    assert _direct_mismatches(_planted_problem_endpoint, {})
    assert not _direct_mismatches(
        _planted_problem_endpoint,
        {
            "_planted_problem_endpoint": {
                "ProblemHTTPException(partial(problems.authentication_required_problem, detail='denied'))"
            }
        },
    )
    assert not _direct_mismatches(_planted_endpoint, {"_planted_endpoint": {"HTTPException(status_code=409)"}})
    owner = f"{_planted_delegated.__module__.split('.')[-1]}._planted_delegated"
    assert owner in _direct_helper_calls(_planted_calls_delegate)
    assert owner not in _direct_helper_calls(_planted_endpoint)
    known = {owner for owners in refusal_inventory_delegated.DIRECT_PRODUCERS.values() for owner in owners}
    covered = set(refusal_inventory_delegated.DIRECT_PRODUCERS[("GET", "/api/fleet/summary")])
    assert _unlisted_known_helpers(_planted_known_helper, known, covered) == {"chunks.get_chunk"}
