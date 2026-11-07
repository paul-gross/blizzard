"""Which operator and board operations reach each shared dependency, read from the app's routes.

An operator or board operation is one under ``/api`` in the OpenAPI document, outside the fleet and the
``/api/auth`` login surface. Each shared dependency is found among a route's effective dependencies, except
the project requirement, which an endpoint may reach from its body and is found by transitive dispatch to
its handler. A permission counts only when a membership role can lack it — every member holds the floor
every role bundle shares, so a gate demanding only that never refuses one.
"""

import inspect
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from blizzard.hub.api import auth_session, flag_guard, hub_level
from blizzard.hub.auth.memberships import MEMBERSHIP_ROLES
from blizzard.hub.auth.permissions import Permission, expand
from tests.support.handler_applicability import transitive_dispatch
from tests.support.runner_refusal_discovery import route_inventory

Operation = tuple[str, str]

_PROJECT_REQUIRED_HANDLER = "blizzard.hub.api.project_params.refuse_project_required"
_PERMISSION_GATES: tuple[tuple[Any, tuple[str, ...]], ...] = (
    (auth_session.require, ("permission",)),
    (auth_session.require_either, ("permission", "alternative")),
    (auth_session.require_hub, ("permission",)),
)


@dataclass(frozen=True)
class OperatorDependencies:
    """The operator operations each shared dependency reaches."""

    store: frozenset[Operation]
    authentication: frozenset[Operation]
    permission: frozenset[Operation]
    tenant: frozenset[Operation]
    project: frozenset[Operation]
    flag: frozenset[Operation]


def _calls(dependant: Any) -> Iterator[Any]:
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _calls(dependency)


def _made_by(call: Any, factory: Any) -> bool:
    code = getattr(call, "__code__", None)
    return code is not None and code in factory.__code__.co_consts


def _gate_permissions(call: Any) -> set[Permission] | None:
    outer = next((names for factory, names in _PERMISSION_GATES if _made_by(call, factory)), None)
    if outer is None:
        return None
    nonlocals = inspect.getclosurevars(call).nonlocals
    return {nonlocals[name] for name in outer}


def operator_operations(app: Any) -> dict[Operation, Any]:
    """Every operator and board operation the OpenAPI document carries, keyed by its declared path."""
    routes, unresolved = route_inventory(app)
    assert not unresolved, unresolved
    return {
        (method, route.path): route
        for (method, _), route in routes.items()
        if route.include_in_schema
        and route.path.startswith("/api/")
        and not route.path.startswith(("/api/fleet/", "/api/auth/"))
    }


def operator_dependencies(app: Any) -> OperatorDependencies:
    """Read each shared dependency's operator operations off ``app``'s routes."""
    floor = frozenset[Permission].intersection(*(expand(role) for role in MEMBERSHIP_ROLES))
    store: set[Operation] = set()
    authentication: set[Operation] = set()
    permission: set[Operation] = set()
    tenant: set[Operation] = set()
    project: set[Operation] = set()
    flag: set[Operation] = set()
    for operation, route in operator_operations(app).items():
        calls = list(_calls(route.dependant))
        if hub_level.hub_services in calls:
            store.add(operation)
        if auth_session.resolve_tenant in calls:
            tenant.add(operation)
            authentication.add(operation)
        gates = [permissions for call in calls if (permissions := _gate_permissions(call)) is not None]
        if any(_made_by(call, auth_session.require_hub) for call in calls):
            authentication.add(operation)
        if any(not permissions <= floor for permissions in gates):
            permission.add(operation)
        if any(_made_by(call, flag_guard.flag_guard) for call in calls):
            flag.add(operation)
        evidence, _ = transitive_dispatch(route, app)
        if _PROJECT_REQUIRED_HANDLER in evidence:
            project.add(operation)
    return OperatorDependencies(
        store=frozenset(store),
        authentication=frozenset(authentication),
        permission=frozenset(permission),
        tenant=frozenset(tenant),
        project=frozenset(project),
        flag=frozenset(flag),
    )
