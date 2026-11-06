import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import FunctionType, ModuleType
from typing import Protocol

import pytest
from fastapi import Depends, FastAPI, HTTPException
from starlette.responses import JSONResponse

from tests.support.runner_refusal_discovery import operation_producers, producer_inventory_failures, route_inventory


class Refusals:
    def dependency(self) -> None:
        self.refuse()

    def refuse(self) -> None:
        raise HTTPException(status_code=409)


service = Refusals()


def endpoint(_: None = Depends(service.dependency)) -> None:
    service.refuse()


async def handler(request: object, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=403, content={})


class Callback(Protocol):
    def run(self) -> None: ...


def dynamic_endpoint(callback: Callback = Depends(lambda: None)) -> None:
    callback.run()


def planted_refusal() -> None:
    raise HTTPException(status_code=418)


def alias_endpoint() -> None:
    invoke = planted_refusal
    invoke()


def annotated_alias_endpoint() -> None:
    invoke: Callable[[], None] = planted_refusal
    invoke()


def rebound_endpoint(callback: Callback = Depends(lambda: None)) -> None:
    invoke = planted_refusal
    invoke = callback.run
    invoke()


class CallableDependency:
    def __call__(self) -> None:
        self.refuse()

    def refuse(self) -> None:
        raise HTTPException(status_code=429)


def callable_endpoint(_: None = Depends(CallableDependency())) -> None:
    pass


class ConstructedRefusal:
    def __init__(self) -> None:
        raise HTTPException(status_code=451)


def constructor_endpoint() -> None:
    ConstructedRefusal()


def early_alias_endpoint() -> None:
    invoke()  # noqa  # pyright: ignore[reportUnboundVariable]  # intentionally before binding
    invoke = planted_refusal  # noqa  # intentionally bound after use


def conditional_alias_endpoint(flag: bool) -> None:
    if flag:
        invoke = planted_refusal
    invoke()  # pyright: ignore[reportPossiblyUnboundVariable]  # intentionally conditional


def shadowed_endpoint() -> None:
    from math import sin as planted_refusal

    planted_refusal(1)


def test_constructor_producer_is_a_candidate_with_unresolved_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    for obj in (constructor_endpoint, ConstructedRefusal, ConstructedRefusal.__init__):
        monkeypatch.setattr(obj, "__module__", "blizzard.hub.planted")
    app = FastAPI()
    app.add_api_route("/constructor", constructor_endpoint, methods=["POST"])
    routes, _ = route_inventory(app)
    operation = ("POST", "/constructor")
    found, unknown = operation_producers(routes[operation], app)
    assert any("ConstructedRefusal.__init__" in item and "451" in item for item in found)
    assert any("constructor dispatch ConstructedRefusal" in item for item in unknown)
    failures = producer_inventory_failures({operation: found}, {operation: set()}, {operation: unknown})
    assert any("missing producer" in item and "451" in item for item in failures)
    assert any("unresolved" in item and "constructor dispatch" in item for item in failures)


@pytest.mark.parametrize("endpoint", [early_alias_endpoint, conditional_alias_endpoint, shadowed_endpoint])
def test_unsafe_aliases_remain_unresolved(endpoint: Callable[..., None], monkeypatch: pytest.MonkeyPatch) -> None:
    for function in (endpoint, planted_refusal):
        monkeypatch.setattr(function, "__module__", "blizzard.hub.planted")
    app = FastAPI()
    app.add_api_route("/unsafe", endpoint, methods=["GET"])
    routes, _ = route_inventory(app)
    found, unknown = operation_producers(routes[("GET", "/unsafe")], app)
    assert not any("planted_refusal" in item and "418" in item for item in found)
    assert any("dynamic call" in item for item in unknown)


def test_local_callable_alias_is_discovered_per_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    for function in (alias_endpoint, planted_refusal):
        monkeypatch.setattr(function, "__module__", "blizzard.hub.planted")
    app = FastAPI()
    app.add_api_route("/alias", alias_endpoint, methods=["POST"])
    routes, _ = route_inventory(app)
    operation = ("POST", "/alias")
    found, unknown = operation_producers(routes[operation], app)
    assert any("planted_refusal" in item and "418" in item for item in found)
    assert not any("dynamic call invoke" in item for item in unknown)
    assert any(
        "missing producer" in item for item in producer_inventory_failures({operation: found}, {operation: set()}, {})
    )


def test_rebound_alias_remains_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rebound_endpoint, "__module__", "blizzard.hub.planted")
    app = FastAPI()
    app.add_api_route("/rebound", rebound_endpoint, methods=["GET"])
    routes, _ = route_inventory(app)
    found, unknown = operation_producers(routes[("GET", "/rebound")], app)
    assert not any("planted_refusal" in item for item in found)
    assert any("dynamic call invoke" in item for item in unknown)


def test_annotated_callable_alias_is_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    for function in (annotated_alias_endpoint, planted_refusal):
        monkeypatch.setattr(function, "__module__", "blizzard.hub.planted")
    app = FastAPI()
    app.add_api_route("/alias", annotated_alias_endpoint, methods=["POST"])
    routes, _ = route_inventory(app)
    found, unknown = operation_producers(routes[("POST", "/alias")], app)
    assert any("planted_refusal" in item and "418" in item for item in found)
    assert not any("dynamic call invoke" in item for item in unknown)


def test_callable_dependency_delegation_is_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    for function in (callable_endpoint, CallableDependency.__call__, CallableDependency.refuse):
        monkeypatch.setattr(function, "__module__", "blizzard.hub.planted")
    app = FastAPI()
    app.add_api_route("/callable", callable_endpoint, methods=["GET"])
    routes, _ = route_inventory(app)
    found, unknown = operation_producers(routes[("GET", "/callable")], app)
    assert any("CallableDependency.refuse" in item and "429" in item for item in found)
    assert not any("dynamic call self.refuse" in item for item in unknown)


def test_bound_dependency_and_global_delegation_and_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    for function in (endpoint, handler, Refusals.dependency, Refusals.refuse):
        monkeypatch.setattr(function, "__module__", "blizzard.hub.planted")
    app = FastAPI()
    app.add_api_route("/test", endpoint, methods=["GET"])
    app.add_exception_handler(ValueError, handler)
    routes, _ = route_inventory(app)
    found, unknown = operation_producers(routes[("GET", "/test")], app)
    assert any("Refusals.refuse" in item and "409" in item for item in found)
    assert any("handler" in item and "403" in item for item in found)
    assert not any("dynamic call self.refuse" in item or "dynamic call service.refuse" in item for item in unknown)
    assert any("exception handler applicability" in item for item in unknown)


def test_dynamic_receiver_remains_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dynamic_endpoint, "__module__", "blizzard.hub.planted")
    app = FastAPI()
    app.add_api_route("/test", dynamic_endpoint, methods=["GET"])
    routes, _ = route_inventory(app)
    _, unknown = operation_producers(routes[("GET", "/test")], app)
    assert any("dynamic call callback.run" in item for item in unknown)


def test_module_delegation_producer_requires_operation_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("from fastapi import HTTPException\ndef refuse():\n raise HTTPException(409)\n")
    endpoint_path = tmp_path / "endpoint.py"
    endpoint_path.write_text("import blizzard.hub.planted_helper as helper\ndef endpoint():\n helper.refuse()\n")
    modules: list[ModuleType] = []
    for name, path in (("blizzard.hub.planted_helper", helper), ("blizzard.hub.planted_endpoint", endpoint_path)):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    app = FastAPI()
    endpoint = vars(modules[1])["endpoint"]
    assert isinstance(endpoint, FunctionType)
    app.add_api_route("/test", endpoint, methods=["GET"])
    routes, _ = route_inventory(app)
    operation = ("GET", "/test")
    found, unknown = operation_producers(routes[operation], app)
    assert any("planted_helper.refuse" in item and "409" in item for item in found)
    failures = producer_inventory_failures({operation: found}, {operation: set()}, {operation: unknown})
    assert any("missing producer" in item and "planted_helper.refuse" in item for item in failures)
