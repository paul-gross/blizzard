"""The ``hub status`` command graph validates and keeps every registration rule (``bzh:dependency-injection``).

Each gate runs over the real registration the verb builds from and over that registration with a planted violation."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.recording import OWNING, Kind, RecordingRegistry
from blizzard.foundation.registry.internal.translation import COMMAND_LIFETIMES
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import Resolver, ScopeValues
from blizzard.hub.cli.container_command import ContainerFleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.status import status
from tests.support.container_guards import (
    alias_identity_violations,
    default_factory_violations,
    duplicate_owners,
    graph_builds,
    legacy_registrations,
    lifetime_violations,
    role_key_violations,
    typed_accessor_violations,
)

type Check = Callable[[RecordingRegistry], list[str]]


def _builds(registry: RecordingRegistry) -> list[str]:
    return graph_builds(registry, COMMAND_LIFETIMES, ScopeValues())


def _lifetimes(registry: RecordingRegistry) -> list[str]:
    return lifetime_violations(registry, COMMAND_LIFETIMES)


class _IRole:
    pass


class _Missing(_IRole):
    pass


class _Needs:
    def __init__(self, missing: _Missing) -> None:
        self.missing = missing


class _Defaulted:
    def __init__(self, limit: int = 3) -> None:
        self.limit = limit


class _TakesResolver:
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver


class _LegacyState:
    pass


def _legacy_state() -> _LegacyState:
    return _LegacyState()


def _second_context(cli: CliContext) -> CliContext:
    return cli


def _plant_missing(registry: RecordingRegistry) -> None:
    registry.service(_Needs, lifetime=Lifetime.COMMAND)


def _plant_second_owner(registry: RecordingRegistry) -> None:
    registry.factory(_second_context, lifetime=Lifetime.COMMAND)


def _plant_other_lifetime(registry: RecordingRegistry) -> None:
    registry.service(_Missing, lifetime=Lifetime.REQUEST)


def _plant_alias_over_two_owners(registry: RecordingRegistry) -> None:
    first = registry.service(_Missing, lifetime=Lifetime.COMMAND)
    registry.service(_Missing, lifetime=Lifetime.COMMAND)
    registry.alias(port(_IRole), first)


def _plant_role_clash(registry: RecordingRegistry) -> None:
    owned = registry.service(_Missing, lifetime=Lifetime.COMMAND)
    registry.alias(port(_IRole), owned)
    registry.alias(port(_IRole), owned)


def _plant_default(registry: RecordingRegistry) -> None:
    registry.service(_Defaulted, lifetime=Lifetime.COMMAND)


def _plant_resolver(registry: RecordingRegistry) -> None:
    registry.service(_TakesResolver, lifetime=Lifetime.COMMAND)


_CHECKS: list[tuple[Check, Callable[[RecordingRegistry], None]]] = [
    (_builds, _plant_missing),
    (duplicate_owners, _plant_second_owner),
    (_lifetimes, _plant_other_lifetime),
    (alias_identity_violations, _plant_alias_over_two_owners),
    (role_key_violations, _plant_role_clash),
    (default_factory_violations, _plant_default),
    (typed_accessor_violations, _plant_resolver),
]


def _status() -> ContainerFleetCommand:
    assert isinstance(status, ContainerFleetCommand)
    return status


@pytest.mark.parametrize(("check", "plant"), _CHECKS)
def test_the_status_graph_passes_each_check_and_fails_its_planted_violation(
    check: Check, plant: Callable[[RecordingRegistry], None]
) -> None:
    assert check(_status().registry()) == []
    planted = _status().registry()
    plant(planted)
    assert check(planted) != []


def test_the_status_graph_declares_no_legacy_registration() -> None:
    assert legacy_registrations(_status().registry()) == []
    planted = LegacyRegistry()
    planted.leaf(_legacy_state, lifetime=Lifetime.COMMAND)
    assert legacy_registrations(planted) != []


def test_every_collaborator_status_declares_is_registered() -> None:
    owned = {entry.key for entry in _status().registry().entries if entry.kind in OWNING or entry.kind is Kind.ALIAS}
    assert set(_status().collaborators.values()) <= owned
