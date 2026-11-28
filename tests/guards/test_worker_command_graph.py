"""Every selected worker graph validates, and each registration guard rejects a planted fault."""

from __future__ import annotations

from collections.abc import Callable

import click
import pytest

from blizzard.cli import command_root
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.recording import OWNING, Kind, RecordingRegistry
from blizzard.foundation.registry.internal.translation import COMMAND_LIFETIMES
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import Resolver, ScopeValues
from blizzard.runner.cli import runner
from blizzard.runner.cli.container_command import ContainerWorkerCommand
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

_PATHS = [
    ("heartbeat",),
    ("session-end",),
    ("ask",),
    ("attach",),
    ("work-items",),
    ("pm-items",),
    *(("chunk", name) for name in ("history", "asks", "notes")),
    *(("artifact", name) for name in ("list", "get", "create", "staged", "commit")),
    *(("garden", name) for name in ("findings", "proposals")),
    *(("finding", name) for name in ("list", "get")),
    ("scope", "list"),
    *(("analytics", "counts", name) for name in ("files", "skills", "agent-types", "nodes")),
    *(("analytics", "spend", name) for name in ("nodes", "graphs")),
    ("opencode", "drive"),
]


def _command(path: tuple[str, ...]) -> ContainerWorkerCommand:
    command: click.Command = runner
    ctx = click.Context(command)
    for name in path:
        assert isinstance(command, click.Group), path
        found = command.get_command(ctx, name)
        assert found is not None, path
        command = found
        ctx = click.Context(command, parent=ctx, info_name=name)
    assert isinstance(command, ContainerWorkerCommand), path
    return command


class _Role:
    pass


class _Missing(_Role):
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


class _Legacy:
    pass


def _legacy() -> _Legacy:
    return _Legacy()


def _missing(registry: RecordingRegistry) -> None:
    registry.service(_Needs, lifetime=Lifetime.COMMAND)


def _duplicate(registry: RecordingRegistry) -> None:
    registry.service(_Missing, lifetime=Lifetime.COMMAND)
    registry.service(_Missing, lifetime=Lifetime.COMMAND)


def _lifetime(registry: RecordingRegistry) -> None:
    registry.service(_Missing, lifetime=Lifetime.REQUEST)


def _alias(registry: RecordingRegistry) -> None:
    first = registry.service(_Missing, lifetime=Lifetime.COMMAND)
    registry.service(_Missing, lifetime=Lifetime.COMMAND)
    registry.alias(port(_Role), first)


def _role(registry: RecordingRegistry) -> None:
    owned = registry.service(_Missing, lifetime=Lifetime.COMMAND)
    registry.alias(port(_Role), owned)
    registry.alias(port(_Role), owned)


def _default(registry: RecordingRegistry) -> None:
    registry.service(_Defaulted, lifetime=Lifetime.COMMAND)


def _resolver(registry: RecordingRegistry) -> None:
    registry.service(_TakesResolver, lifetime=Lifetime.COMMAND)


type Check = Callable[[RecordingRegistry], list[str]]

_CHECKS: list[tuple[Check, Callable[[RecordingRegistry], None]]] = [
    (lambda graph: graph_builds(graph, COMMAND_LIFETIMES, ScopeValues()), _missing),
    (duplicate_owners, _duplicate),
    (lambda graph: lifetime_violations(graph, COMMAND_LIFETIMES), _lifetime),
    (alias_identity_violations, _alias),
    (role_key_violations, _role),
    (default_factory_violations, _default),
    (typed_accessor_violations, _resolver),
]


@pytest.mark.parametrize("path", _PATHS, ids=lambda path: " ".join(path))
@pytest.mark.parametrize(("check", "plant"), _CHECKS)
def test_every_worker_graph_passes_each_guard_and_rejects_its_planted_fault(
    path: tuple[str, ...], check: Check, plant: Callable[[RecordingRegistry], None]
) -> None:
    command = _command(path)
    assert check(command_root.registered(command.areas())) == []
    planted = command_root.registered(command.areas())
    plant(planted)
    assert check(planted)


@pytest.mark.parametrize("path", _PATHS, ids=lambda path: " ".join(path))
def test_every_worker_collaborator_is_registered_without_legacy(path: tuple[str, ...]) -> None:
    command = _command(path)
    graph = command_root.registered(command.areas())
    keys = {entry.key for entry in graph.entries if entry.kind in OWNING or entry.kind is Kind.ALIAS}
    assert not legacy_registrations(graph)

    def unregistered(collaborators: list[type[object]]) -> list[type[object]]:
        return [collaborator for collaborator in collaborators if collaborator not in keys]

    declared = list(command.collaborators.values())
    assert unregistered(declared) == []
    assert unregistered([*declared, _Missing]) == [_Missing]
    planted = LegacyRegistry()
    for register in command.areas():
        register(planted)
    planted.leaf(_legacy, lifetime=Lifetime.COMMAND)
    assert legacy_registrations(planted) == ["_Legacy is a legacy leaf"]
