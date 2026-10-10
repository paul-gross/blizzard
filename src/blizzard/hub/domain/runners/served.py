"""A runner's served declaration — what adding and registering a runner store.

Each entry is a project id or slug, stored with the id of the project it resolved to
(``blizzard-product:/plans/projects/hub/spec/eligibility.md`` §The served declaration). Adding a runner may refuse an
entry; registering never does, because a registration is also the runner's heartbeat."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from blizzard.hub.domain.runners.registration import ServedEntry


class IServedProjects(Protocol):
    """The tenant's projects as a declaration resolves against them, read afresh on every call;
    :class:`~blizzard.hub.domain.projects.bulk_resolve.BulkProjectResolver` is one."""

    def resolve_ids(self, references: Sequence[str]) -> Mapping[str, str | None]:
        """Each distinct reference with the id of the project it names, ``None`` when none does — in one read."""
        ...

    def only_id(self) -> str | None:
        """The id of the tenant's project when it holds exactly one, else ``None``."""
        ...


class UnknownServedProject(Exception):
    """Adding a runner named entries that resolve to no project of the tenant — lists them, as declared."""

    def __init__(self, entries: tuple[str, ...]) -> None:
        super().__init__(f"no project answers to {', '.join(entries)}")
        self.entries = entries


def resolve_served(entries: Sequence[str], projects: IServedProjects) -> tuple[ServedEntry, ...]:
    """Each distinct entry, in declared order, with the id of the project it resolves to — ``None`` when none
    answers. An empty declaration asks for no resolution."""
    if not entries:
        return ()
    ids = projects.resolve_ids(entries)
    return tuple(ServedEntry(entry, ids.get(entry)) for entry in dict.fromkeys(entries))


def declaration_at_add(sent: Sequence[str] | None, projects: IServedProjects) -> tuple[ServedEntry, ...]:
    """The declaration a runner is added with. Naming none, it serves the tenant's only project, stored by id so no
    slug change touches it, and nothing when the tenant holds several. Naming entries, each must resolve:
    :class:`UnknownServedProject` lists the ones that do not. ``[]`` serves nothing."""
    if sent is not None:
        declared = resolve_served(sent, projects)
        unknown = tuple(entry.entry for entry in declared if not entry.resolved())
        if unknown:
            raise UnknownServedProject(unknown)
        return declared
    only = projects.only_id()
    return () if only is None else (ServedEntry(only, only),)


def declaration_at_registration(
    stored: tuple[ServedEntry, ...], sent: Sequence[str] | None, projects: IServedProjects
) -> tuple[ServedEntry, ...] | None:
    """The declaration a registration stores, or ``None`` to leave the stored one untouched. A sent list (``[]``
    included) replaces it, every entry resolved afresh and an unresolved one kept with no project. Sending ``None``
    keeps it and resolves again only its unresolved entries, so an entry starts serving once a project answers to it
    while a resolved one stays on its id. A stored declaration with nothing unresolved asks for no resolution, and one
    whose unresolved entries still resolve to nothing is left untouched."""
    if sent is not None:
        return resolve_served(sent, projects)
    unresolved = [entry.entry for entry in stored if not entry.resolved()]
    if not unresolved:
        return None
    found = {entry: project_id for entry, project_id in projects.resolve_ids(unresolved).items() if project_id}
    if not found:
        return None
    return tuple(entry if entry.resolved() else ServedEntry(entry.entry, found.get(entry.entry)) for entry in stored)
