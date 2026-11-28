"""Every route's container dependencies, resolved at the lifetime the route runs in.

A route reaches the container through each ``inject_sync`` callable in its dependency tree, whose ``FromDishka``
parameters name the types it resolves. A route whose tree admits a tenant
(:func:`~blizzard.hub.api.auth_session.tenant_request`) resolves them beneath that tenant's scope, every other route
through the process scope. Each helper returns the problems it finds."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import UnionType
from typing import Annotated, Any, cast, get_args, get_origin, get_type_hints

from dishka import FromDishka
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant

from blizzard.foundation.registry.interface import Lifetime, ServiceAbsent, port
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, open_child, supply
from blizzard.hub.api.auth_session import tenant_request
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support.route_classes import api_routes

#: The marker ``FromDishka`` annotates a parameter with.
_MARKER: type[object] = type(cast("object", get_args(FromDishka[object])[1]))

#: The tenant a route's tenant scope is opened for; the scope reads no store, so the tenant need not exist.
_TENANT = TenantId("ten_00000000000000000000000000")


@dataclass(frozen=True)
class RouteDependency:
    """One type a route resolves through the container, and whether the route runs beneath an admitted tenant."""

    route: str
    tenant: bool
    dependency: type[Any] | UnionType


def _calls(dependant: Dependant) -> Iterator[Callable[..., Any]]:
    if dependant.call is not None:
        yield dependant.call
    for child in dependant.dependencies:
        yield from _calls(child)


def _container_types(call: Callable[..., Any]) -> Iterator[type[Any] | UnionType]:
    original = getattr(call, "__dishka_orig_func__", None)
    if original is None:
        return
    for hint in get_type_hints(original, include_extras=True).values():
        if get_origin(hint) is Annotated and any(isinstance(meta, _MARKER) for meta in hint.__metadata__):
            yield get_args(hint)[0]


def _name(dependency: type[Any] | UnionType) -> str:
    return str(dependency) if isinstance(dependency, UnionType) else dependency.__qualname__


def route_dependencies(app: FastAPI) -> list[RouteDependency]:
    """Every type each of ``app``'s routes resolves through the container."""
    found: list[RouteDependency] = []
    for route in api_routes(app):
        calls = list(_calls(route.dependant))
        tenant = tenant_request in calls
        name = f"{sorted(route.methods or ())[0]} {route.path}"
        seen: set[type[Any] | UnionType] = set()
        for call in calls:
            for dependency in _container_types(call):
                if dependency not in seen:
                    seen.add(dependency)
                    found.append(RouteDependency(name, tenant, dependency))
    return found


def unresolved_process_dependencies(dependencies: list[RouteDependency], process: OwnedChild) -> list[str]:
    """Each route dependency that does not resolve from ``process``, for a process whose routes admit no tenant —
    the runner host's."""
    problems: list[str] = []
    for each in dependencies:
        try:
            process.resolver.get(each.dependency)
        except Exception as error:  # every refusal is a finding this gate reports
            problems.append(f"{each.route}: {_name(each.dependency)} in the process: {type(error).__name__}")
    return problems


def runner_route_dependencies(dependencies: list[RouteDependency]) -> list[str]:
    """Each container dependency a ``/runners/{id}`` route takes; such a route reads its runner's seams."""
    return [
        f"{each.route}: {_name(each.dependency)}"
        for each in dependencies
        if each.route.split(" ")[1].startswith("/runners/")
    ]


def unrefused_absent_dependencies(dependencies: list[RouteDependency], process: OwnedChild) -> list[str]:
    """Each route dependency ``process`` does not refuse as absent, for a graph that declares every service absent."""
    problems: list[str] = []
    for each in dependencies:
        try:
            process.resolver.get(each.dependency)
        except ServiceAbsent:
            continue
        except Exception as error:  # any other refusal answers 500, not the absent service's 503
            problems.append(f"{each.route}: {_name(each.dependency)} is not absent: {type(error).__name__}")
        else:
            problems.append(f"{each.route}: {_name(each.dependency)} resolves")
    return problems


def unresolved_route_dependencies(dependencies: list[RouteDependency], process: OwnedChild) -> list[str]:
    """Each route dependency that does not resolve at its route's lifetime beneath ``process``."""
    tenant = open_child(
        process,
        lifetime=Lifetime.TENANT,
        owner=_TENANT,
        values=ScopeValues((supply(port(StoreScope), StoreScope(_TENANT)),)),
    )
    request = open_child(tenant, lifetime=Lifetime.REQUEST, owner=_TENANT, values=ScopeValues())
    problems: list[str] = []
    try:
        for each in dependencies:
            resolver = request.resolver if each.tenant else process.resolver
            try:
                resolver.get(each.dependency)
            except Exception as error:  # every refusal is a finding this gate reports
                where = "a tenant request" if each.tenant else "the process"
                problems.append(f"{each.route}: {_name(each.dependency)} in {where}: {type(error).__name__}")
    finally:
        request.close()
        tenant.close()
    return problems
