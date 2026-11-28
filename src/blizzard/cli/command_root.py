"""The shared CLI command root: one validated ``COMMAND``-lifetime container per invocation, closed as it ends.

Areas register; this module only composes them, builds the container, resolves what the command declares, and closes
it. It names no daemon's type, so any CLI's verbs open their container here."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import contextmanager

from blizzard.foundation.registry.interface import Registry
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import COMMAND_LIFETIMES, build_container
from blizzard.foundation.registry.scopes import ScopeValues

#: One area's registration function.
type Area = Callable[[Registry], None]
#: What a command's areas declared: the registry's record, named here so a verb's root imports no ``internal/``.
type Recorded = RecordingRegistry


def registered(areas: Iterable[Area]) -> Recorded:
    """What ``areas`` declare, recorded and not yet built."""
    registry = RecordingRegistry()
    for register in areas:
        register(registry)
    return registry


@contextmanager
def command_scope(
    registry: Recorded, *, owner: str, values: ScopeValues, collaborators: Mapping[str, type[object]]
) -> Generator[dict[str, object]]:
    """Build one command's container over the borrowed ``values`` and yield its ``collaborators``, each under its name.

    The container closes when the block ends, and a failure inside it — resolution's included — is handed to the
    finalizers. The caller gets the instances only, never the container or a resolver."""
    container = build_container(registry, COMMAND_LIFETIMES, owner=owner, values=values)
    try:
        yield {name: container.resolver.get(wanted) for name, wanted in collaborators.items()}
    except BaseException as exc:
        container.close(exc)
        raise
    container.close()
