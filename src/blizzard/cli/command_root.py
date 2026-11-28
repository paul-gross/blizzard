"""The shared CLI command root: one validated ``COMMAND``-lifetime container per invocation, closed as it ends.

Areas register; this module only composes them, builds the container, resolves what the command declares, and closes
it. It names no daemon's type, so any CLI's verbs open their container here."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import contextmanager

import click

from blizzard.foundation.registry.interface import Registry
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import COMMAND_LIFETIMES, build_container
from blizzard.foundation.registry.scopes import ScopeValues

#: One area's registration function.
type Area = Callable[[Registry], None]


def registered(areas: Iterable[Area]) -> RecordingRegistry:
    """What ``areas`` declare, recorded and not yet built."""
    registry = RecordingRegistry()
    for register in areas:
        register(registry)
    return registry


@contextmanager
def command_scope(
    areas: Iterable[Area], *, owner: str, values: ScopeValues, collaborators: Mapping[str, type[object]]
) -> Generator[dict[str, object]]:
    """Build one command's container from ``areas`` over the borrowed ``values``; yield ``collaborators`` by name.

    It closes when the block ends, handing finalizers any failure, resolution's included. A finalizer failing then is
    warned about and the command's own failure still raised. The caller never gets the container or a resolver."""
    container = build_container(registered(areas), COMMAND_LIFETIMES, owner=owner, values=values)
    try:
        yield {name: container.resolver.get(wanted) for name, wanted in collaborators.items()}
    except BaseException as exc:
        try:
            container.close(exc)
        except ExceptionGroup as failures:
            for failure in failures.exceptions:
                click.echo(f"warning: cleanup failed: {type(failure).__name__}: {failure}", err=True)
        raise
    container.close()
