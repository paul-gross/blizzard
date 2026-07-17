"""The PM work-source seam — a vendor-native pass-through read.

The hub reads a chunk's PM item (issue body + comment thread) straight from the
forge on demand and **never stores its contents**: the pointer is the
durable referent, the item is fetched fresh. The domain owns this Protocol
(``bzh:dependency-inversion``); a vendor-shaped adapter under ``internal/`` implements
it against a real forge — the ``blizzard-mock`` forge in tests, GitHub in production —
one instance per configured ``[[pm_source]]``, pinned to its own repo and
carrying its own credentialed client.

D-110 grows the seam beyond ``fetch``: a binding also owns parsing its own ingest-token
form, rendering the board-legible label, and deriving the pointer's/a branch's browser
address — grammar that used to live in the domain-layer ``pm/label.py`` module (a
``bzh:domain-core`` violation once there was more than one provider). The
:class:`IPmSourceRegistry` replaces the single ``pm_source: IPmSource | None``
seam slot: the hub builds one binding per declared source, and an empty registry is a
legal hub with no PM reach.

D-111 gives ``parse`` its production caller: ``POST /chunks`` takes source-native
tokens, and :meth:`IPmSourceRegistry.resolve` walks the configured bindings, returning
the first pointer one claims. Exactly one binding can ever claim a token — config
rejects a duplicate ``name`` and a duplicate ``(provider, repo)`` — so ``parse``
returns ``None`` for "not my token" rather than raising: the registry loops cleanly over
every binding, and the route is what raises/reports when nothing claims it (422, D-109).

``fetch`` returns a small domain :class:`PmItem`; the edge maps it onto a wire
:class:`~blizzard.wire.chunk.PmItemEntry` with the pointer, its label, and a ``fetched_at``.

D-107 gives the pointer its own ``source`` name, so finding a pointer's binding is a
plain registry lookup (``registry.get(pointer.source)``) — the D-109 repo-matching
``owns`` this seam carried through Phase 2, while the pointer had no source name of
its own, is retired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from blizzard.hub.domain.work import PmPointer


@dataclass(frozen=True)
class PmItem:
    """A pass-through PM item — title, body, and comment bodies, vendor-native."""

    body: str
    title: str = ""
    comments: list[str] = field(default_factory=list)


class PmSourceError(Exception):
    """The forge read failed — an unreachable forge or an unresolvable pointer."""


class IPmSource(Protocol):
    """One configured, credentialed PM binding."""

    def parse(self, token: str) -> PmPointer | None:
        """This source's own ingest-token form into a pointer, or ``None`` when
        ``token`` is not shaped for this source — the registry's :meth:`resolve`
         tries each configured source in turn and 422s when none claims it."""
        ...

    def fetch(self, pointer: PmPointer) -> PmItem:
        """Fetch a pointer's body + comments from the forge, never storing them."""
        ...

    def label(self, pointer: PmPointer) -> str | None:
        """The board-legible label for ``pointer`` — ``None`` when it can't be rendered
        (e.g. a URL that isn't shaped like this source's items)."""
        ...

    def web_url(self, pointer: PmPointer) -> str | None:
        """The pointer's browser-openable address, or ``None`` when it can't be derived."""
        ...

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        """The forge's browser ``tree`` address for ``branch_name`` on ``repo``, or
        ``None`` when this source has no web origin to link through."""
        ...


class IPmSourceRegistry(Protocol):
    """The hub's configured PM sources, looked up by their declared ``name``."""

    def get(self, name: str) -> IPmSource | None:
        """The binding declared under ``name``, or ``None`` when none is configured."""
        ...

    def names(self) -> list[str]:
        """Every configured source's name."""
        ...

    def resolve(self, token: str) -> PmPointer | None:
        """The first configured binding's :meth:`IPmSource.parse` of ``token`` that
        claims it, or ``None`` when none do. Exactly one binding can ever
        claim a token — config rejects a duplicate ``name`` (unambiguous
        ``name:ref``/``name#ref``) and a duplicate ``(provider, repo)`` (a URL maps to
        at most one source) — so which binding is tried first never matters."""
        ...
