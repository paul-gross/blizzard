"""The PM work-source seam (D-047/D-084/D-106/D-108) — a vendor-native pass-through read.

The hub reads a chunk's PM item (issue body + comment thread) straight from the
forge on demand and **never stores its contents** (D-047): the pointer is the
durable referent, the item is fetched fresh. The domain owns this Protocol
(``bzh:dependency-inversion``); a vendor-shaped adapter under ``internal/`` implements
it against a real forge — the ``blizzard-mock`` forge in tests, GitHub in production —
one instance per configured ``[[pm_source]]`` (D-106), pinned to its own repo and
carrying its own credentialed client.

D-108 grows the seam beyond ``fetch``: a binding also owns parsing its own ingest-token
form, rendering the board-legible label, and deriving the pointer's/a branch's browser
address — grammar that used to live in the domain-layer ``pm/label.py`` module (a
``bzh:domain-core`` violation once there was more than one provider). The
:class:`IPmSourceRegistry` (D-106) replaces the single ``pm_source: IPmSource | None``
seam slot: the hub builds one binding per declared source, and an empty registry is a
legal hub with no PM reach.

``fetch`` returns a small domain :class:`PmItem`; the edge maps it onto a wire
:class:`~blizzard.wire.chunk.PmItemEntry` with the pointer, its label, and a ``fetched_at``.

D-107 (Phase 2) adds ``owns``: ingest-time resolution and the board label/fetch both
need to find *which* configured source claims a given pointer, and repo-matching is
the only way while the pointer itself carries no source name — see
:func:`blizzard.hub.pm.registry.resolve_source`, the one resolver both call sites share.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from blizzard.hub.domain.work import PmPointer


@dataclass(frozen=True)
class PmItem:
    """A pass-through PM item — body and comment bodies, vendor-native (D-047)."""

    body: str
    comments: list[str] = field(default_factory=list)


class PmSourceError(Exception):
    """The forge read failed — an unreachable forge or an unresolvable pointer."""


class UnknownSource(Exception):
    """A raw ingest token — or, at a registry miss, a name — no configured source owns."""


class IPmSource(Protocol):
    """One configured, credentialed PM binding (D-047/D-106/D-108)."""

    def parse(self, token: str) -> PmPointer:
        """This source's own ingest-token form into a pointer.

        Raises :class:`UnknownSource` when ``token`` is not shaped for this source —
        Phase 2's ingest-time resolution tries each configured source in turn."""
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

    def owns(self, pointer: PmPointer) -> bool:
        """True when ``pointer`` names this source's own configured repo (D-107).

        The repo-matching resolution ingest-time rejection and the board label/fetch
        both need: parse ``pointer.url`` for its owner/repo and compare against this
        binding's own configured repo. This phase (D-105 not yet landed) the pointer
        carries no source name, so this is the only way to find a pointer's binding."""
        ...


class IPmSourceRegistry(Protocol):
    """The hub's configured PM sources, looked up by their declared ``name`` (D-106)."""

    def get(self, name: str) -> IPmSource | None:
        """The binding declared under ``name``, or ``None`` when none is configured."""
        ...

    def names(self) -> list[str]:
        """Every configured source's name."""
        ...
