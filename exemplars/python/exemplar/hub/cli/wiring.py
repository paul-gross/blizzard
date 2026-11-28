"""The permits verbs' registrations: the one service they own, with the finalizer that closes it."""

# exemplar: bzh:dependency-injection — a new command service is one line here and one collaborator at its verb.

from __future__ import annotations

from collections.abc import Iterator

from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.hub.cli.context import CliContext
from exemplar.hub.cli.permit_reader import PermitReader


def _open_permit_reader(cli: CliContext) -> Iterator[PermitReader]:
    reader = PermitReader(cli)
    yield reader
    reader.report()


def register(registry: Registry) -> None:
    """Declare the permit reader, owned by the command and closed as it ends, however it ends."""
    registry.resource(_open_permit_reader, lifetime=Lifetime.COMMAND)
