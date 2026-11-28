"""Guard helpers for registered graphs and registry import boundaries.

Each helper returns the problems it finds, empty when none, so a caller asserts on the whole list and a planted case can
prove the helper fails what it should. The graph helpers take a ``RecordingRegistry``; the boundary helpers scan source."""

from __future__ import annotations

import ast
import inspect
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import cast, get_args

from blizzard.foundation.registry.internal.recording import OWNING, Kind, RecordingRegistry, Registration
from blizzard.foundation.registry.internal.translation import (
    GraphRefused,
    ProcessLifetimes,
    build_container,
)
from blizzard.foundation.registry.scopes import OwnedChild, Resolver, ScopeValues
from blizzard.hub.process_root import HubProcess
from blizzard.hub.scopes.tenants import TenantContainers

_INTERFACE = "blizzard.foundation.registry.interface"
_REGISTRY_PACKAGE = ("foundation", "registry")
#: The route edges, the hub's and the runner host's, and the one dishka module each may import: the FastAPI
#: integration's ``FromDishka`` and ``inject_sync``.
_ROUTE_EDGES = (("hub", "api"), ("runner", "api"))
_ROUTE_EDGE_DISHKA = "dishka.integrations.fastapi"

#: The CLI's command roots, relative to the source root; they compose areas and register nothing themselves.
CLI_ROOTS: tuple[Path, ...] = (Path("cli/command_root.py"), Path("hub/cli/container_command.py"))

#: The roots that compose adapters and areas, so the wiring boundary exempts them: the CLI's, the hub's process root and
#: hosted app, and the runner host's root, composition and hosted app.
PROCESS_ROOTS: frozenset[Path] = frozenset(
    {
        Path("hub/process_root.py"),
        Path("hub/app.py"),
        Path("runner/composition.py"),
        Path("runner/host_root.py"),
        Path("runner/app.py"),
        *CLI_ROOTS,
    }
)

#: The hub's roots, relative to the source root: they compose areas and orchestrate lifecycles, and register nothing
#: themselves.
HUB_ROOTS: tuple[Path, ...] = (Path("hub/process_root.py"), Path("hub/app.py"), Path("hub/composition.py"))

#: The ``Registry`` verbs that declare a registration, and the legacy vocabulary's two.
REGISTRATION_VERBS = frozenset(
    {"service", "factory", "resource", "context", "alias", "absent", "optional", "leaf", "bag"}
)

#: The names that reach a hub services bag: the tenant and hub-level bags, the stores and core they were built from,
#: the request bridge's dependencies, and the chunk read bundle. No module under ``src/blizzard`` names one.
BAG_NAMES = frozenset(
    {
        "TenantServices",
        "HubServices",
        "TenantStores",
        "HubCore",
        "TenantCache",
        "ServedTenant",
        "TenantServing",
        "IOpenTenantServices",
        "TenantServicesOpener",
        "TenantServiceParts",
        "Services",
        "Hub",
        "hub_services",
        "tenant_services",
        "ChunkReadStores",
    }
)

#: The generic resolvers and process handles a registered provider never takes: each reaches every service behind it.
#: A provider names the types it uses.
RESOLVER_TYPES: frozenset[type] = frozenset({Resolver, HubProcess, TenantContainers, OwnedChild})

#: The registration kinds whose source the container calls with resolved parameters.
_CONSTRUCTED = (Kind.SERVICE, Kind.FACTORY, Kind.RESOURCE, Kind.LEAF, Kind.BAG)


def graph_builds(registry: RecordingRegistry, lifetimes: ProcessLifetimes, values: ScopeValues) -> list[str]:
    """The reason the registered graph does not build under strict validation, if it does not."""
    try:
        build_container(registry, lifetimes, owner="guard", values=values).close()
    except GraphRefused as refused:
        return [str(refused)]
    return []


def duplicate_owners(registry: RecordingRegistry) -> list[str]:
    """Keys more than one registration owns."""
    owners = Counter(entry.key for entry in registry.entries if entry.kind in OWNING)
    return [f"{_name(key)} has {count} owners" for key, count in owners.items() if count > 1]


def lifetime_violations(registry: RecordingRegistry, lifetimes: ProcessLifetimes) -> list[str]:
    """Registrations at a lifetime the process does not use."""
    return [
        f"{_name(entry.key)} is registered at {entry.lifetime.value}, not on {[life.value for life in lifetimes.path]}"
        for entry in registry.entries
        if entry.lifetime is not None and entry.lifetime not in lifetimes.path
    ]


def alias_identity_violations(registry: RecordingRegistry) -> list[str]:
    """Aliases that do not resolve to exactly one owned registration."""
    owned = Counter(entry.key for entry in registry.entries if entry.kind in OWNING)
    return [
        f"alias {_name(entry.key)} targets {_name(entry.target_key)}, which has {owned[entry.target_key]} owners"
        for entry in registry.entries
        if entry.kind is Kind.ALIAS and owned[entry.target_key] != 1
    ]


def role_key_violations(registry: RecordingRegistry) -> list[str]:
    """A role key (an alias or owned key) registered twice, so two roles would share one constructor annotation."""
    keys = Counter(entry.key for entry in registry.entries if entry.kind in OWNING or entry.kind is Kind.ALIAS)
    return [f"{_name(key)} is the key of {count} registrations" for key, count in keys.items() if count > 1]


def default_factory_violations(registry: RecordingRegistry) -> list[str]:
    """Services and factories whose parameters carry a default, which only an explicit factory may pass."""
    problems: list[str] = []
    for entry in registry.entries:
        if entry.kind not in (Kind.SERVICE, Kind.FACTORY, Kind.RESOURCE):
            continue
        problems.extend(_defaulted(entry))
    return problems


def _defaulted(entry: Registration) -> Iterable[str]:
    for name, parameter in inspect.signature(entry.source).parameters.items():
        if parameter.default is not inspect.Parameter.empty:
            yield f"{_name(entry.key)} parameter {name!r} has a default"


def dishka_importers(source_root: Path) -> list[str]:
    """Modules under ``source_root`` that import dishka from outside ``foundation/registry/internal/``; a route edge
    module under ``hub/api/`` or ``runner/api/`` may import ``dishka.integrations.fastapi`` and nothing else of dishka."""
    problems: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).parts
        if relative[:3] == (*_REGISTRY_PACKAGE, "internal"):
            continue
        edge = relative[:2] in _ROUTE_EDGES
        if any(_is_dishka(name) and not (edge and _is_route_edge_dishka(name)) for name in _imports(path)):
            problems.append(str(path.relative_to(source_root)))
    return problems


def _is_dishka(name: str) -> bool:
    return name == "dishka" or name.startswith("dishka.")


def _is_route_edge_dishka(name: str) -> bool:
    return name == _ROUTE_EDGE_DISHKA or name.startswith(f"{_ROUTE_EDGE_DISHKA}.")


def child_open_violations(root: Path, *, exempt: tuple[tuple[str, ...], ...] = (_REGISTRY_PACKAGE,)) -> list[str]:
    """Modules under ``root`` that call ``.open_child(...)`` directly, outside the exempt package paths.

    ``open_child`` the function is the one door to a child scope; the owner's method of the same name is private to
    ``foundation/registry/`` and the tests of that owner."""
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(relative.parts[: len(prefix)] == prefix for prefix in exempt):
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "open_child":
                problems.append(f"{relative}:{node.lineno}")
    return problems


#: What a domain area's wiring may import beyond the domain itself: the foundation and the tenancy vocabulary.
_DOMAIN_WIRING_REACH = ("blizzard.hub.domain.", "blizzard.foundation.", "blizzard.hub.tenancy")


def wiring_boundary_violations(source_root: Path, *, roots: frozenset[Path] = PROCESS_ROOTS) -> list[str]:
    """Wiring modules (those importing the registry interface) that reach another area's wiring or an adapter, and
    domain wiring modules that reach outside the domain.

    An adapter package's own wiring module may import that package's ``internal/``; the process ``roots`` compose
    adapters and areas, and are exempt. A domain area's wiring names only domain types, the foundation, and the
    tenancy vocabulary: what lives outside the domain is the root's to hand in, or its owner's to register."""
    problems: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root)
        if relative.parts[:2] == _REGISTRY_PACKAGE or relative in roots:
            continue
        imports = _imports(path)
        if _INTERFACE not in imports:
            continue
        own_area = _module(relative).rpartition(".")[0]
        reported: set[str] = set()
        for name in sorted(imports):
            if not name.startswith("blizzard."):
                continue
            parts = name.split(".")
            if "internal" in parts:
                adapter = ".".join(parts[: parts.index("internal") + 2])
                if _is_wiring(_module(relative)) and ".".join(parts[: parts.index("internal")]) == own_area:
                    continue
                if adapter not in reported:
                    reported.add(adapter)
                    problems.append(f"{relative}: imports the adapter {adapter}")
            elif _is_wiring(name) and name.rpartition(".")[0] != own_area:
                problems.append(f"{relative}: imports another area's wiring {name}")
            elif (
                relative.parts[:2] == ("hub", "domain")
                and not name.startswith(_DOMAIN_WIRING_REACH)
                and name.rpartition(".")[0] not in imports
            ):
                problems.append(f"{relative}: imports {name} from outside the domain")
    return problems


def bag_violations(source_root: Path) -> list[str]:
    """Each place a module under ``source_root`` names a services bag — an import, a definition, a name, or an
    attribute."""
    problems: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            named: str | None = None
            if isinstance(node, ast.alias):
                named = node.name.rpartition(".")[2]
            elif isinstance(node, ast.Name):
                named = node.id
            elif isinstance(node, ast.Attribute):
                named = node.attr
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                named = node.name
            if named in BAG_NAMES:
                problems.append(f"{relative}:{getattr(node, 'lineno', 0)}: names {named}")
    return sorted(problems)


def root_registration_violations(source_root: Path, roots: Iterable[Path] = HUB_ROOTS) -> list[str]:
    """Each call of a registration verb in one of ``roots``: a root composes areas, whose wiring registers."""
    problems: list[str] = []
    for relative in roots:
        for node in ast.walk(ast.parse((source_root / relative).read_text(), filename=str(relative))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in REGISTRATION_VERBS
            ):
                problems.append(f"{relative}:{node.lineno}: calls .{node.func.attr}(...)")
    return sorted(problems)


def legacy_registrations(registry: RecordingRegistry) -> list[str]:
    """Each legacy leaf or bag ``registry`` declares."""
    return [
        f"{_name(entry.key)} is a legacy {entry.kind.value}"
        for entry in registry.entries
        if entry.kind in (Kind.LEAF, Kind.BAG)
    ]


def typed_accessor_violations(registry: RecordingRegistry) -> list[str]:
    """Each registered provider or service parameter that takes a generic resolver or process handle, however it is
    wrapped (``X | None``, a ``Callable``'s arguments, a generic's parameters)."""
    problems: list[str] = []
    for entry in registry.entries:
        if entry.kind not in _CONSTRUCTED:
            continue
        for name, parameter in inspect.signature(entry.source, eval_str=True).parameters.items():
            reached = sorted(_name(each) for each in _types_in(parameter.annotation) if each in RESOLVER_TYPES)
            problems.extend(f"{_name(entry.source)} parameter {name!r} takes {each}" for each in reached)
    return problems


def _types_in(annotation: object) -> Iterable[type]:
    if isinstance(annotation, (list, tuple)):
        for each in cast("Iterable[object]", annotation):
            yield from _types_in(each)
        return
    if isinstance(annotation, type):
        yield annotation
    for argument in get_args(annotation):
        yield from _types_in(argument)


def _is_wiring(module: str) -> bool:
    return module.rpartition(".")[2] == "wiring"


def _module(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(["blizzard", *parts])


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _name(key: object) -> str:
    return getattr(key, "__qualname__", repr(key))
