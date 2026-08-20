"""The work-source seam — one binding per named source, one shape for every kind.

For a configured, credentialed binding a work item's contents are **never stored**: the
pointer is the durable referent, the item is fetched fresh from the forge. The built-in
``hub`` source (issue #357) is the one exception — its own store *is* the item's system
of record, so its "fetch" is a read of durable state, not a forge round-trip; every other
binding still keeps the pass-through contract this docstring describes. A binding also
owns parsing its own ingest-token form, its label, and its browser addresses
(``bzh:domain-core``). ``parse`` returns ``None`` for "not my token" rather than raising,
so a registry can loop over every binding."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from blizzard.hub.domain.work import WorkRef
from blizzard.hub.work_sources.annotator import IWorkAnnotator
from blizzard.hub.work_sources.closer import IWorkCloser
from blizzard.hub.work_sources.editor import IWorkEditor


@dataclass(frozen=True)
class WorkItem:
    """A pass-through work item — title, body, and comment bodies, vendor-native."""

    body: str
    title: str = ""
    comments: list[str] = field(default_factory=list)


class WorkSourceError(Exception):
    """The forge read failed — an unreachable forge or an unresolvable pointer."""


class IWorkSource(Protocol):
    """One configured, credentialed work-source binding."""

    def parse(self, token: str) -> WorkRef | None:
        """This source's own ingest-token form into a pointer, or ``None`` when
        ``token`` is not shaped for this source — the registry's :meth:`resolve`
         tries each configured source in turn and 422s when none claims it."""
        ...

    def fetch(self, pointer: WorkRef) -> WorkItem:
        """A pointer's body + comments, read fresh — from the forge for a configured
        binding (never stored here), or from this hub's own store for the built-in
        ``hub`` source, whose row *is* the durable content."""
        ...

    def label(self, pointer: WorkRef) -> str | None:
        """The board-legible label for ``pointer`` — ``None`` when it can't be rendered
        (e.g. a URL that isn't shaped like this source's items)."""
        ...

    def web_url(self, pointer: WorkRef) -> str | None:
        """The pointer's browser-openable address, or ``None`` when it can't be derived."""
        ...

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        """The forge's browser ``tree`` address for ``branch_name`` on ``repo``, or
        ``None`` when this source has no web origin to link through."""
        ...


class IWorkSourceRegistry(Protocol):
    """The hub's configured work sources, looked up by their declared ``name``."""

    def get(self, name: str) -> IWorkSource | None:
        """The binding declared under ``name``, or ``None`` when none is configured."""
        ...

    def names(self) -> list[str]:
        """Every configured source's name."""
        ...

    def annotator(self, name: str) -> IWorkAnnotator | None:
        """The binding declared under ``name``'s write half, or ``None`` when
        that source is unconfigured or not opted into annotation — the
        structural "never written to" a non-opted source gets."""
        ...

    def annotating_names(self) -> list[str]:
        """Every source name with an annotator built — the opted-in subset of
        :meth:`names`."""
        ...

    def closer(self, name: str) -> IWorkCloser | None:
        """The binding declared under ``name``'s close half, or ``None`` when
        that source is unconfigured or not opted into closing — the structural
        "never closed" a non-opted source gets."""
        ...

    def closing_names(self) -> list[str]:
        """Every source name with a closer built — the opted-in subset of
        :meth:`names`."""
        ...

    def editor(self, name: str) -> IWorkEditor | None:
        """The binding declared under ``name``'s editor half, or ``None`` when that
        source has no browsable item surface — the structural "never edited" a
        non-editing source gets."""
        ...

    def resolve(self, token: str) -> WorkRef | None:
        """The first configured binding's :meth:`IWorkSource.parse` of ``token`` that
        claims it, or ``None`` when none do. Exactly one binding can ever
        claim a token — config rejects a duplicate ``name`` (unambiguous
        ``name:ref``/``name#ref``) and a duplicate ``(provider, repo)`` (a URL maps to
        at most one source) — so which binding is tried first never matters."""
        ...
