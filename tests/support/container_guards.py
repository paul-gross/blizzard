"""Guard helpers for registered graphs and registry import boundaries.

Each helper returns the problems it finds, empty when none, so a caller asserts on the whole list and a planted case can
prove the helper fails what it should. The graph helpers take a ``RecordingRegistry``; the boundary helpers scan source."""

from __future__ import annotations

import ast
import inspect
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from blizzard.foundation.registry.internal.recording import OWNING, Kind, RecordingRegistry, Registration
from blizzard.foundation.registry.internal.translation import (
    GraphRefused,
    ProcessLifetimes,
    build_container,
)
from blizzard.foundation.registry.scopes import ScopeValues

_INTERFACE = "blizzard.foundation.registry.interface"
_REGISTRY_PACKAGE = ("foundation", "registry")
#: The one dishka module a route edge may import: the FastAPI integration's ``FromDishka`` and ``inject_sync``.
_ROUTE_EDGE = ("hub", "api")
_ROUTE_EDGE_DISHKA = "dishka.integrations.fastapi"

#: The hub's composition roots, relative to the source root: the process root, the legacy bridge it registers, and the
#: hosted app over it. Each composes adapters and areas, so the wiring boundary exempts it.
PROCESS_ROOTS: frozenset[Path] = frozenset(
    {Path("hub/process_root.py"), Path("hub/composition.py"), Path("hub/app.py")}
)


#: The route modules whose area resolves through the container, relative to the source root: each names the services
#: it uses and never the legacy bags.
NARROWED_MODULES: tuple[Path, ...] = tuple(
    Path("hub/api") / name
    for name in (
        "findings.py",
        "graphs.py",
        "admin_settings.py",
        "config.py",
        "secrets.py",
        "forges.py",
        "credentials.py",
        "repositories.py",
        "work_sources.py",
        "work_source_params.py",
        "credential_choice.py",
        "credential_params.py",
        "forge_choice.py",
        "forge_params.py",
        "forge_warnings.py",
        "repository_params.py",
        "record_reads.py",
        "user_names.py",
        "request_config.py",
        "projects.py",
        "project_params.py",
        "project_labels.py",
        "project_label_fields.py",
        "urgencies.py",
        "settings.py",
        "hosts.py",
        "fleet_host.py",
        "auth_session.py",
        "auth.py",
    )
)
#: The legacy bags a narrowed module may not name.
_LEGACY_BAGS = frozenset({"Services", "TenantServices", "Hub", "HubServices", "hub_services"})


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
    module under ``hub/api/`` may import ``dishka.integrations.fastapi`` and nothing else of dishka."""
    problems: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).parts
        if relative[:3] == (*_REGISTRY_PACKAGE, "internal"):
            continue
        edge = relative[:2] == _ROUTE_EDGE
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


def wiring_boundary_violations(source_root: Path, *, roots: frozenset[Path] = PROCESS_ROOTS) -> list[str]:
    """Wiring modules (those importing the registry interface) that reach another area's wiring or an adapter.

    An adapter package's own wiring module may import that package's ``internal/``; the process ``roots`` compose
    adapters and areas, and are exempt."""
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
    return problems


def narrowing_violations(source_root: Path, modules: Iterable[Path] = NARROWED_MODULES) -> list[str]:
    """Each place a narrowed module names a legacy bag — an import, a name, or an attribute."""
    problems: list[str] = []
    for relative in modules:
        tree = ast.parse((source_root / relative).read_text(), filename=str(relative))
        for node in ast.walk(tree):
            named: str | None = None
            if isinstance(node, ast.alias):
                named = node.name.rpartition(".")[2]
            elif isinstance(node, ast.Name):
                named = node.id
            elif isinstance(node, ast.Attribute):
                named = node.attr
            if named in _LEGACY_BAGS:
                problems.append(f"{relative}:{getattr(node, 'lineno', 0)}: names {named}")
    return sorted(problems)


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
