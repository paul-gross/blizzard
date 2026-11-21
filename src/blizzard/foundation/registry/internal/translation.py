"""The dishka translation: recorded registrations become one strictly validated container per process.

Each process maps the lifetimes it uses onto its own consecutive dishka scopes; any other lifetime is refused, never
mapped to a different one. A graph the validator rejects raises ``GraphRefused`` with the dishka error as its cause, so
no dishka type escapes this package."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dishka import STRICT_VALIDATION, BaseScope, Provider, make_container, new_scope
from dishka.exception_base import DishkaError

from blizzard.foundation.registry.interface import Lifetime, ServiceAbsent
from blizzard.foundation.registry.internal.recording import (
    Kind,
    RecordingRegistry,
    Registration,
    Rewrites,
    keys_by_lifetime,
    merged,
)
from blizzard.foundation.registry.internal.resolver import ContainerOwner, refuse_unregistered_values
from blizzard.foundation.registry.scopes import ScopeValues
from blizzard.foundation.roles import adapter_model


class GraphRefused(Exception):
    """The registrations do not make a valid graph for the process; a dishka error is chained as the cause."""


class _HubScopes(BaseScope):
    PROCESS = new_scope("process")
    TENANT = new_scope("tenant")
    REQUEST = new_scope("request")


class _HostScopes(BaseScope):
    PROCESS = new_scope("process")
    RUNNER = new_scope("runner")
    OPERATION = new_scope("operation")


class _CommandScopes(BaseScope):
    COMMAND = new_scope("command")


@adapter_model
@dataclass(frozen=True)
class ProcessLifetimes:
    """The ordered lifetime path a process maps onto consecutive non-skipped scopes of its own."""

    path: tuple[Lifetime, ...]
    scope_class: type[BaseScope]
    scopes: Mapping[Lifetime, BaseScope] = field(repr=False)


HUB_LIFETIMES = ProcessLifetimes(
    path=(Lifetime.PROCESS, Lifetime.TENANT, Lifetime.REQUEST),
    scope_class=_HubScopes,
    scopes={
        Lifetime.PROCESS: _HubScopes.PROCESS,
        Lifetime.TENANT: _HubScopes.TENANT,
        Lifetime.REQUEST: _HubScopes.REQUEST,
    },
)
HOST_LIFETIMES = ProcessLifetimes(
    path=(Lifetime.PROCESS, Lifetime.RUNNER, Lifetime.OPERATION),
    scope_class=_HostScopes,
    scopes={
        Lifetime.PROCESS: _HostScopes.PROCESS,
        Lifetime.RUNNER: _HostScopes.RUNNER,
        Lifetime.OPERATION: _HostScopes.OPERATION,
    },
)
COMMAND_LIFETIMES = ProcessLifetimes(
    path=(Lifetime.COMMAND,),
    scope_class=_CommandScopes,
    scopes={Lifetime.COMMAND: _CommandScopes.COMMAND},
)


def build_container(
    recorded: RecordingRegistry,
    lifetimes: ProcessLifetimes,
    *,
    owner: str,
    values: ScopeValues,
    overrides: Rewrites | None = None,
) -> ContainerOwner:
    """Validate the recorded graph for ``lifetimes`` and open its root container, which ``values`` supply."""
    entries = merged(recorded.entries, overrides)
    for entry in entries:
        if entry.lifetime is not None and entry.lifetime not in lifetimes.scopes:
            raise GraphRefused(
                f"{entry.lifetime.value} is not a lifetime of this process; it has "
                f"{[lifetime.value for lifetime in lifetimes.path]}"
            )
    _refuse_bag_dependents(entries)
    provider = Provider()
    for entry in entries:
        _provide(provider, entry, lifetimes)
    contexts = keys_by_lifetime(entries, Kind.CONTEXT)
    context = values.as_context()
    refuse_unregistered_values(contexts, lifetimes.path[0], context)
    try:
        container = make_container(
            provider,
            scopes=lifetimes.scope_class,
            context=context,
            lock_factory=threading.Lock,
            validation_settings=STRICT_VALIDATION,
        )
    except DishkaError as error:
        raise GraphRefused(str(error)) from error
    return ContainerOwner(container, lifetimes=lifetimes.path, owner=owner, contexts=contexts)


def _refuse_bag_dependents(entries: tuple[Registration, ...]) -> None:
    """Refuse any registration but a bag that depends on a bag key, directly or through an alias or an optional."""
    bags = {entry.key for entry in entries if entry.kind is Kind.BAG}
    if not bags:
        return
    for entry in entries:
        if entry.kind is Kind.BAG:
            continue
        for dependency in _dependencies(entry):
            if dependency in bags:
                raise GraphRefused(
                    f"{_label(entry)} depends on the legacy bag {_label_of(dependency)}; only a bag may depend on a bag"
                )


def _dependencies(entry: Registration) -> list[object]:
    match entry.kind:
        case Kind.ALIAS | Kind.OPTIONAL:
            return [] if entry.target_key is None else [entry.target_key]
        case Kind.SERVICE | Kind.FACTORY | Kind.RESOURCE | Kind.LEAF:
            _check_parameters(entry)
            return [p.annotation for p in inspect.signature(entry.source, eval_str=True).parameters.values()]
        case _:
            return []


def _label(entry: Registration) -> str:
    return _label_of(entry.key)


def _label_of(key: object) -> str:
    return getattr(key, "__qualname__", repr(key))


def _provide(provider: Provider, entry: Registration, lifetimes: ProcessLifetimes) -> None:
    scope = None if entry.lifetime is None else lifetimes.scopes[entry.lifetime]
    key: Any = entry.key
    match entry.kind:
        case Kind.SERVICE | Kind.FACTORY | Kind.RESOURCE | Kind.LEAF | Kind.BAG:
            _check_parameters(entry)
            provider.provide(entry.source, scope=scope, provides=key)
        case Kind.CONTEXT:
            provider.from_context(provides=key, scope=scope)
        case Kind.ALIAS:
            provider.alias(source=entry.target_key, provides=key)  # pyright: ignore[reportArgumentType]  # a registered key is a class
        case Kind.ABSENT:
            provider.provide(_refusing(entry.label), scope=scope, provides=key)
        case Kind.OPTIONAL:
            provider.provide(_optional(entry.target_key, key), scope=scope, provides=key)
        case Kind.VALUE:
            provider.provide(_supplied(entry.source), scope=scope, provides=key)


def _check_parameters(entry: Registration) -> None:
    """Refuse a constructor or factory the container cannot fill by annotation alone."""
    what = getattr(entry.source, "__qualname__", repr(entry.source))
    try:
        signature = inspect.signature(entry.source, eval_str=True)
    except (NameError, TypeError, ValueError) as error:
        raise GraphRefused(f"{what} has a signature that cannot be evaluated: {error}") from error
    for name, parameter in signature.parameters.items():
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise GraphRefused(f"{what} takes {parameter.kind.description} {name!r}; register an explicit factory")
        if parameter.annotation is inspect.Parameter.empty:
            raise GraphRefused(f"{what} parameter {name!r} has no annotation to resolve it by")
        if parameter.default is not inspect.Parameter.empty:
            raise GraphRefused(f"{what} parameter {name!r} has a default; register an explicit factory that passes it")


def _refusing(label: str) -> Callable[[], Any]:
    def refuse() -> Any:
        raise ServiceAbsent(label)

    return refuse


def _supplied(value: object) -> Callable[[], Any]:
    def supply_value() -> Any:
        return value

    return supply_value


def _optional(target_key: object, optional_key: object) -> Callable[..., Any]:
    if target_key is None:

        def none() -> None:
            return None

        return none

    def build(value: Any) -> Any:
        return value

    build.__annotations__ = {"value": target_key, "return": optional_key}
    return build
