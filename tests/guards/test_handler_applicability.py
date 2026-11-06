"""Registered exception dispatch remains visible independently of producer traversal."""

from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pytest import MonkeyPatch

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.api import project_params
from blizzard.hub.app import create_app_for_export
from tests.support.handler_applicability import (
    applicability_failures,
    dispatch_diagnostic,
    dispatch_review_failures,
    handler_inventory,
    transitive_dispatch,
)
from tests.support.handler_applicability_baseline import DIRECT_DISPATCH, HANDLERS, OPERATIONS
from tests.support.handler_applicability_fixtures import local_import_caller
from tests.support.repo_files import repo_root
from tests.support.runner_refusal_discovery import (
    direct_exception_dispatch,
    match_operations,
    route_inventory,
    runner_boundary,
)


def _callback() -> str:
    return "first"


def _replacement_callback() -> str:
    return "second"


def _invoke(callback: object) -> None:
    pass


async def _callback_handler(request: Request, exc: Exception) -> JSONResponse:
    _invoke(_callback)
    return JSONResponse({"detail": "refused"})


def test_runner_operations_review_every_handler_registration() -> None:
    app = create_app_for_export(packaged_flag_registry())
    routes, route_unknown = route_inventory(app)
    reaches, unknown = runner_boundary(repo_root())
    matched, missing = match_operations(reaches, set(routes))
    assert not route_unknown and not unknown and not missing, (route_unknown, unknown, missing)
    operations = {operation for group in matched.values() for operation in group}
    reviewed: dict[tuple[str, str], set[str]] = {(method, path): set(HANDLERS) for method, path in OPERATIONS}
    assert not (failures := applicability_failures(operations, handler_inventory(app), reviewed)), "\n".join(failures)
    direct = {operation: direct_exception_dispatch(routes[operation], app) for operation in operations}
    assert {operation: evidence for operation, evidence in direct.items() if evidence} == DIRECT_DISPATCH


class _BaseError(Exception):
    pass


class _ChildError(_BaseError):
    pass


def _raising_dependency() -> None:
    raise _ChildError()


def _dependent_endpoint(_: None = Depends(_raising_dependency)) -> None:
    pass


def _plain_endpoint() -> None:
    pass


def _shadowed_endpoint(_ChildError: type[Exception]) -> None:
    raise _ChildError()


def _helper_raises() -> None:
    raise _ChildError()


def _helper_caller() -> None:
    _helper_raises()


def _dynamic_caller(callback: object) -> None:
    callback()  # pyright: ignore[reportCallIssue] - Exercise an unresolved dynamic call in the AST fixture.


def _safe_builtins(value: object) -> None:
    id(value)
    callable(value)
    type(value)


def _shadowed_builtin(len: object) -> None:
    len()  # pyright: ignore[reportCallIssue] - Exercise a shadowed builtin in the AST fixture.


def _dynamic_builtin_arguments(value: object) -> None:
    str(value)
    type("Dynamic", (), {})


class _Factory:
    @classmethod
    def build(cls) -> None:
        raise _ChildError()

    @staticmethod
    def check() -> None:
        raise _BaseError()

    def instance(self) -> None:
        raise _ChildError()


def _factory_caller() -> None:
    _Factory.build()
    _Factory.check()


def _unknown_method(value: str) -> None:
    value.strip()


def _shadowed_factory(_Factory: Any) -> None:
    _Factory.build()


def test_transitive_dispatch_resolves_class_descriptors_without_guessing_instance_types() -> None:
    app = FastAPI()
    for path, endpoint in (
        ("/factory", _factory_caller),
        ("/unknown", _unknown_method),
        ("/shadow", _shadowed_factory),
    ):
        app.add_api_route(path, endpoint, methods=["GET"])
    app.add_exception_handler(_BaseError, _callback_handler)
    routes, _ = route_inventory(app)
    evidence, unresolved = transitive_dispatch(routes[("GET", "/factory")], app)
    assert not unresolved
    items = {item for values in evidence.values() for item in values}
    assert any("_Factory.build raises" in item for item in items)
    assert any("_Factory.check raises" in item for item in items)
    assert any("value.strip()" in item for item in transitive_dispatch(routes[("GET", "/unknown")], app)[1])
    assert any("_Factory.build()" in item for item in transitive_dispatch(routes[("GET", "/shadow")], app)[1])


def test_transitive_dispatch_resolves_local_module_and_safe_builtins() -> None:
    app = FastAPI()
    for path, endpoint in (
        ("/import", local_import_caller),
        ("/builtins", _safe_builtins),
        ("/shadow", _shadowed_builtin),
    ):
        app.add_api_route(path, endpoint, methods=["GET"])
    app.add_exception_handler(_BaseError, _callback_handler)
    app.add_exception_handler(ValueError, _callback_handler)
    routes, _ = route_inventory(app)
    evidence, unresolved = transitive_dispatch(routes[("GET", "/import")], app)
    assert not unresolved
    assert any("imported_helper raises" in item for items in evidence.values() for item in items)
    assert not transitive_dispatch(routes[("GET", "/builtins")], app)[1]
    assert any("unresolved call len()" in item for item in transitive_dispatch(routes[("GET", "/shadow")], app)[1])
    app.add_api_route("/dynamic-builtins", _dynamic_builtin_arguments, methods=["GET"])
    routes, _ = route_inventory(app)
    unresolved = transitive_dispatch(routes[("GET", "/dynamic-builtins")], app)[1]
    assert any("unresolved call str(value)" in item for item in unresolved)
    assert any("unresolved call type(" in item for item in unresolved)


def test_transitive_dispatch_finds_helper_and_exposes_unresolved_calls() -> None:
    app = FastAPI()
    app.add_api_route("/helper", _helper_caller, methods=["GET"])
    app.add_api_route("/dynamic", _dynamic_caller, methods=["GET"])
    app.add_exception_handler(_BaseError, _callback_handler)
    routes, _ = route_inventory(app)
    evidence, unresolved = transitive_dispatch(routes[("GET", "/helper")], app)
    assert not unresolved
    assert any("_helper_raises raises" in item for items in evidence.values() for item in items)
    _, unresolved = transitive_dispatch(routes[("GET", "/dynamic")], app)
    assert any("unresolved call callback()" in item for item in unresolved)


def test_transitive_review_detects_new_dispatch_and_dynamic_call() -> None:
    app = FastAPI()
    app.add_api_route("/helper", _helper_caller, methods=["GET"])
    app.add_api_route("/dynamic", _dynamic_caller, methods=["GET"])
    app.add_exception_handler(_BaseError, _callback_handler)
    routes, _ = route_inventory(app)
    helper = ("GET", "/helper")
    dynamic = ("GET", "/dynamic")
    actual: dict[tuple[str, str], tuple[dict[str, set[str]], set[str]]] = {
        op: transitive_dispatch(routes[op], app) for op in (helper, dynamic)
    }
    failures = dispatch_review_failures(actual, {helper: ({}, set()), dynamic: ({}, set())})
    assert any("/helper" in failure and "new dispatch" in failure for failure in failures)
    assert any("/dynamic" in failure and "unreviewed unresolved" in failure for failure in failures)
    assert not dispatch_review_failures(actual, actual)
    diagnostic = "\n".join(dispatch_diagnostic(actual))
    assert "GET /helper" in diagnostic and "_helper_raises raises" in diagnostic
    assert "GET /dynamic" in diagnostic and "unresolved call callback()" in diagnostic


def test_direct_dispatch_is_operation_specific_and_uses_nearest_handler() -> None:
    app = FastAPI()
    app.add_api_route("/dependent", _dependent_endpoint, methods=["GET"])
    app.add_api_route("/plain", _plain_endpoint, methods=["GET"])
    app.add_api_route("/shadowed", _shadowed_endpoint, methods=["GET"])
    app.add_exception_handler(_BaseError, _callback_handler)
    routes, _ = route_inventory(app)
    evidence = direct_exception_dispatch(routes[("GET", "/dependent")], app)
    assert any(
        "_raising_dependency raises" in item and "_ChildError" in item for items in evidence.values() for item in items
    )
    assert not direct_exception_dispatch(routes[("GET", "/plain")], app)
    assert not direct_exception_dispatch(routes[("GET", "/shadowed")], app)

    async def child_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({}, status_code=418)

    app.add_exception_handler(_ChildError, child_handler)
    changed = direct_exception_dispatch(routes[("GET", "/dependent")], app)
    assert changed != evidence
    assert any("child_handler" in handler for handler in changed)


def test_planted_handler_and_replacement_require_operation_specific_review() -> None:
    app = FastAPI()
    original = handler_inventory(app)
    first, second = ("GET", "/one"), ("GET", "/two")

    @app.exception_handler(ValueError)
    async def planted(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": "refused"}, status_code=409)

    changed = handler_inventory(app)
    failures = applicability_failures({first, second}, changed, {first: changed, second: original})
    assert len(failures) == 1 and "/two" in failures[0] and "ValueError" in failures[0]

    @app.exception_handler(ValueError)
    async def replacement(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": "different"}, status_code=418)

    failures = applicability_failures({first}, handler_inventory(app), {first: changed})
    assert any("replacement" in failure for failure in failures)
    assert any("stale handler" in failure and "planted" in failure for failure in failures)


def test_new_and_removed_operations_require_review() -> None:
    failures = applicability_failures({("POST", "/new")}, set(), {("GET", "/old"): set()})
    assert any("missing handler applicability review" in failure for failure in failures)
    assert any("stale operation" in failure for failure in failures)


def test_changed_handler_dependency_requires_review(monkeypatch: MonkeyPatch) -> None:
    app = create_app_for_export(packaged_flag_registry())
    original = handler_inventory(app)

    def replacement(*args: object, **kwargs: object) -> None:
        pass

    replacement.__module__ = "blizzard.hub.api.problems"
    monkeypatch.setattr(project_params, "project_ref", replacement)
    changed = handler_inventory(app)
    failures = applicability_failures({("GET", "/one")}, changed, {("GET", "/one"): original})
    assert any("transitive" in failure and "ProjectRequired" in failure for failure in failures)


def test_referenced_callback_change_requires_review(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(_callback, "__module__", "blizzard.test_callbacks")
    monkeypatch.setattr(_replacement_callback, "__module__", "blizzard.test_callbacks")
    app = FastAPI()
    app.add_exception_handler(ValueError, _callback_handler)
    original = handler_inventory(app)

    monkeypatch.setitem(_callback_handler.__globals__, "_callback", _replacement_callback)
    changed = handler_inventory(app)

    failures = applicability_failures({("GET", "/one")}, changed, {("GET", "/one"): original})
    assert any("unreviewed" in failure and "transitive" in failure for failure in failures)
    assert any("stale handler" in failure and "transitive" in failure for failure in failures)
