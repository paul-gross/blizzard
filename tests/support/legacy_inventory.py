"""The legacy leaves and bags the hub's registry may still declare, and the source modules that may still name the hub's
legacy bag, each named with the area that owns it.

A hub service is born in its area's wiring; a legacy leaf or bag is transitional, so the registry may declare only
those listed here, and an entry whose registration is gone is stale. Adopting a value removes its entry. Both
inventories only shrink: a module that stops naming the bag leaves :data:`BAG_CONSUMERS`, and none joins it."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

from blizzard.foundation.registry.internal.recording import Kind, RecordingRegistry

#: Each legacy key the hub registry declares (a class or a callable alias), and the area that owns it until adopted.
LEGACY_INVENTORY: Mapping[object, str] = {}


def inventory_violations(registry: RecordingRegistry, allowed: Mapping[object, str] = LEGACY_INVENTORY) -> list[str]:
    """Each leaf or bag ``registry`` declares that ``allowed`` does not list, and each listed key it no longer
    declares."""
    declared = {entry.key: entry.kind for entry in registry.entries if entry.kind in (Kind.LEAF, Kind.BAG)}
    problems = [
        f"{getattr(key, '__qualname__', key)} is an unlisted legacy {kind.value}"
        for key, kind in declared.items()
        if key not in allowed
    ]
    problems.extend(
        f"{getattr(key, '__qualname__', key)} ({area}) is listed but no longer declared"
        for key, area in allowed.items()
        if key not in declared
    )
    return problems


#: The names that reach the hub's legacy bag: the bag, the request bridge's dependency, and the core it is built from.
HUB_BAG_NAMES = frozenset({"HubServices", "Hub", "hub_services", "HubCore"})

#: Each module under ``src/blizzard`` that still names the hub's legacy bag, and the area owning it until it retires.
BAG_CONSUMERS: Mapping[Path, str] = {}


def bag_consumer_violations(source_root: Path, allowed: Mapping[Path, str] = BAG_CONSUMERS) -> list[str]:
    """Each module under ``source_root`` naming the hub's legacy bag — an import, a name, or an attribute — that
    ``allowed`` does not list, and each listed module that no longer names it."""
    naming = {path.relative_to(source_root) for path in sorted(source_root.rglob("*.py")) if _names_hub_bag(path)}
    problems = [
        f"{relative} names the hub's legacy bag but is unlisted" for relative in sorted(naming - allowed.keys())
    ]
    problems.extend(
        f"{relative} ({area}) is listed but no longer names the hub's legacy bag"
        for relative, area in sorted(allowed.items())
        if relative not in naming
    )
    return problems


def _names_hub_bag(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.alias):
            named = node.name.rpartition(".")[2]
        elif isinstance(node, ast.Name):
            named = node.id
        elif isinstance(node, ast.Attribute):
            named = node.attr
        else:
            continue
        if named in HUB_BAG_NAMES:
            return True
    return False
