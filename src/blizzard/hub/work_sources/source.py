"""The work-source seam — one binding per named source, one shape for every kind.

A work item's contents are **never stored** by the seam: the pointer is the durable
referent, the item is fetched fresh from its source. A binding also
owns parsing its own ingest-token form, its label, and its browser addresses
(``bzh:domain-core``). ``parse`` returns ``None`` for "not my token" rather than raising,
so a registry can loop over every binding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.auth.models import User
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.domain.chunk.model import WorkItemAuthor, WorkItemAuthorKind, WorkRef
from blizzard.hub.work_sources.annotator import IWorkAnnotator
from blizzard.hub.work_sources.closer import IWorkCloser
from blizzard.hub.work_sources.editor import IWorkEditor


@domain_model
@dataclass(frozen=True)
class AuthorView:
    """A work item's author, resolved legible for display — a login for
    a human author, or the runner/chunk/node lineage for a fleet-authored one. ``kind``
    mirrors :class:`~blizzard.hub.domain.chunk.model.WorkItemAuthorKind`'s value."""

    kind: str
    user_id: str | None = None
    login: str | None = None
    runner_id: str | None = None
    chunk_id: str | None = None
    node_name: str | None = None


def resolve_author_view(author: WorkItemAuthor, users: IReadUserRepository | Mapping[str, User]) -> AuthorView:
    """A ``WorkItemAuthor`` resolved legible for display — the one place a
    ``user_id`` is ever resolved to a login, shared by every pass-through read and
    listing view alike, one lookup at a time or replayed over a pre-resolved batch."""
    if author.kind is WorkItemAuthorKind.USER:
        user = users.get(author.user_id) if author.user_id is not None else None
        return AuthorView(
            kind=author.kind.value, user_id=author.user_id, login=user.username if user is not None else None
        )
    return AuthorView(
        kind=author.kind.value, runner_id=author.runner_id, chunk_id=author.chunk_id, node_name=author.node_name
    )


@domain_model
@dataclass(frozen=True)
class WorkItem:
    """A pass-through work item — title, body, and comment bodies, vendor-native, plus
    the display-resolved author and stated priority, ``None`` when the binding has
    none to give."""

    body: str
    title: str = ""
    comments: list[str] = field(default_factory=lambda: [])
    author: AuthorView | None = None
    stated_priority: str | None = None


class WorkSourceError(Exception):
    """The forge read failed — an unreachable forge or an unresolvable pointer."""

    def __init__(self, message: str = "", *, retry_after: datetime | None = None) -> None:
        super().__init__(message)
        #: Set only when the forge's rate budget turned the call away or the forge refused it for rate: when to retry.
        self.retry_after = retry_after


class IWorkSource(Protocol):
    """One configured, credentialed work-source binding."""

    def parse(self, token: str) -> WorkRef | None:
        """This source's own ingest-token form into a pointer, or ``None`` when
        ``token`` is not shaped for this source."""
        ...

    def fetch(self, pointer: WorkRef) -> WorkItem:
        """A pointer's body + comments, read fresh from the source on every call."""
        ...

    def label(self, pointer: WorkRef) -> str | None:
        """A short human-readable name for ``pointer`` — ``None`` when it can't be rendered
        (e.g. a URL that isn't shaped like this source's items)."""
        ...

    def web_url(self, pointer: WorkRef, *, live_holder: str | None) -> str | None:
        """The pointer's browser-openable address, or ``None`` when it can't be derived.
        ``live_holder`` is the pointer's live (non-terminal) chunk holder, or ``None`` when
        it has none — a required keyword, since a source whose address depends on
        liveness must never mistake an unresolved value for "no holder". A source whose
        address doesn't depend on liveness ignores it."""
        ...

    def forge_reference(self, pointer: WorkRef) -> str | None:
        """The forge's own cross-link for ``pointer`` (``owner/repo#12``), or ``None`` off-forge."""
        ...


class IWorkSourceRegistry(Protocol):
    """The hub's work sources, each looked up by its key — ``hub`` for the built-in source, a configured source's id
    otherwise; only :meth:`resolve` reads a slug."""

    def get(self, source: str) -> IWorkSource | None:
        """The binding keyed ``source``, or ``None`` when none is configured."""
        ...

    def names(self) -> list[str]:
        """Every active source's current slug, and ``hub`` — the handles an ingest token may name."""
        ...

    def source_label(self, source: str) -> str:
        """The handle the source keyed ``source`` displays under: a configured source's current slug, ``hub`` for the
        built-in one, and ``source`` itself when it names no source."""
        ...

    def label(self, pointer: WorkRef) -> str:
        """``pointer`` as displayed: its binding's :meth:`IWorkSource.label`, else ``{source label}#{ref}`` — always
        the source's current slug, never the stored key."""
        ...

    def annotator(self, source: str) -> IWorkAnnotator | None:
        """The write half of the binding keyed ``source``, or ``None`` when that source is unconfigured or not opted
        into annotation — the structural "never written to" a non-opted source gets."""
        ...

    def annotating_ids(self) -> list[str]:
        """Every active source with an annotator built, by id — the opted-in subset of the configured sources."""
        ...

    def label_clearer(self, source: str) -> IWorkAnnotator | None:
        """The write half of the source keyed ``source`` whether or not it annotates;
        ``None`` when no configured source has that key or it has no forge labels."""
        ...

    def closer(self, source: str) -> IWorkCloser | None:
        """The close half of the binding keyed ``source``: always for ``hub``; else ``None`` while
        ``close_forge_writes_enabled`` is false, or when ``source`` names no source or a retired one that no
        longer builds. Raises :class:`WorkSourceError` when an active source fails to build."""
        ...

    def editor(self, source: str) -> IWorkEditor | None:
        """The editor half of the binding keyed ``source``, or ``None`` when that source has no browsable item
        surface — the structural "never edited" a non-editing source gets."""
        ...

    def resolve(self, token: str) -> WorkRef | None:
        """The pointer an ingest token names, or ``None`` when no binding claims it. A ``{x}:{n}``/``{x}#{n}`` token
        whose ``x`` is ``hub`` goes to the built-in source; ``x`` naming a configured source by id, current or former
        slug reaches its :meth:`IWorkSource.parse` alone, rewritten to its id — none, if retired. Any other token goes
        to every active binding; config rejects a duplicate ``(provider, locator)``, so one claims it at most."""
        ...
