"""Permit domain — a project's grant of one capability to one holder, and the verbs that move it."""

# exemplar: bzh:domain-orchestration-split, bzh:domain-core, bzh:repository-split, bzh:bulk-reconstitution,
# bzh:data-roles.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.projects.model import ProjectId
from exemplar.foundation.permits import PermitState, PermitVerb


class PermitFactKind(StrEnum):
    """What one appended permit fact records."""

    GRANTED = "granted"
    SUSPENDED = "suspended"
    RESUMED = "resumed"
    REVOKED = "revoked"


_LIVE: frozenset[PermitState] = frozenset(PermitState) - {PermitState.REVOKED}

#: The states each :class:`PermitVerb` is legal from; any other refuses with :class:`PermitVerbRefused`.
PERMIT_VERB_LEGALITY: Mapping[PermitVerb, frozenset[PermitState]] = MappingProxyType(
    {
        PermitVerb.GRANT: frozenset({PermitState.REQUESTED}),
        PermitVerb.SUSPEND: frozenset({PermitState.GRANTED}),
        PermitVerb.RESUME: frozenset({PermitState.SUSPENDED}),
        PermitVerb.REVOKE: _LIVE,
    }
)

_STATE_AFTER: Mapping[PermitFactKind, PermitState] = MappingProxyType(
    {
        PermitFactKind.GRANTED: PermitState.GRANTED,
        PermitFactKind.SUSPENDED: PermitState.SUSPENDED,
        PermitFactKind.RESUMED: PermitState.GRANTED,
        PermitFactKind.REVOKED: PermitState.REVOKED,
    }
)


@domain_model
@dataclass(frozen=True)
class PermitFact:
    """One fact to append to a permit's history."""

    permit_id: str
    kind: PermitFactKind
    at: datetime
    by: str
    #: Why the permit was revoked; present on a revocation only.
    note: str | None = None


@domain_model
@dataclass(frozen=True)
class Permit:
    """A permit and the newest fact recorded on it."""

    permit_id: str
    project_id: ProjectId
    #: Who the permit is for — a name, not an id.
    holder: str
    capability: str
    requested_at: datetime
    requested_by: str
    latest: PermitFact | None = None

    @classmethod
    def request(
        cls, permit_id: str, *, project_id: ProjectId, holder: str, capability: str, by: str, at: datetime
    ) -> Permit:
        """A fresh permit under ``permit_id`` in ``project_id``, requested ``at`` by ``by``."""
        return cls(
            permit_id=permit_id,
            project_id=project_id,
            holder=holder,
            capability=capability,
            requested_at=at,
            requested_by=by,
        )

    def state(self) -> PermitState:
        """The state the newest fact leaves (``bzh:facts-not-status``); requested before any."""
        if self.latest is None:
            return PermitState.REQUESTED
        return _STATE_AFTER[self.latest.kind]

    def permits(self, verb: PermitVerb) -> bool:
        """Whether ``verb`` is legal from this permit's state (:data:`PERMIT_VERB_LEGALITY`)."""
        return self.state() in PERMIT_VERB_LEGALITY[verb]

    def legal_verbs(self) -> tuple[PermitVerb, ...]:
        """Every verb :meth:`permits`, in declaration order."""
        return tuple(verb for verb in PermitVerb if self.permits(verb))

    def grant(self, *, by: str, at: datetime) -> PermitFact:
        """The grant fact to append."""
        return self._fact(PermitVerb.GRANT, PermitFactKind.GRANTED, by=by, at=at)

    def suspend(self, *, by: str, at: datetime) -> PermitFact:
        """The suspension fact to append."""
        return self._fact(PermitVerb.SUSPEND, PermitFactKind.SUSPENDED, by=by, at=at)

    def resume(self, *, by: str, at: datetime) -> PermitFact:
        """The resumption fact to append."""
        return self._fact(PermitVerb.RESUME, PermitFactKind.RESUMED, by=by, at=at)

    def revoke(self, *, note: str, by: str, at: datetime) -> PermitFact:
        """The revocation fact to append, carrying ``note`` stripped; a blank one raises
        :class:`RevocationNoteRequired`."""
        self._refuse_unless(PermitVerb.REVOKE)
        text = note.strip()
        if not text:
            raise RevocationNoteRequired(self.permit_id)
        return PermitFact(permit_id=self.permit_id, kind=PermitFactKind.REVOKED, at=at, by=by, note=text)

    def _fact(self, verb: PermitVerb, kind: PermitFactKind, *, by: str, at: datetime) -> PermitFact:
        self._refuse_unless(verb)
        return PermitFact(permit_id=self.permit_id, kind=kind, at=at, by=by)

    def _refuse_unless(self, verb: PermitVerb) -> None:
        if not self.permits(verb):
            raise PermitVerbRefused(self.permit_id, verb=verb, state=self.state())


@domain_model
@dataclass(frozen=True)
class PermitPage:
    """One bounded page of :meth:`IReadPermitRepository.list_page`; ``next_cursor`` is ``None`` on the last."""

    permits: tuple[Permit, ...]
    next_cursor: str | None


class IReadPermitRepository(Protocol):
    """Read-only permit access."""

    def get(self, permit_id: str) -> Permit | None: ...

    def get_many(self, permit_ids: Sequence[str]) -> dict[str, Permit]:
        """``get``'s batched sibling, keyed by id; an id that resolves to nothing is dropped."""
        ...

    def list_page(self, *, project: ProjectId | None, holder: str | None, limit: int, cursor: str | None) -> PermitPage:
        """Permits newest requested first, narrowed to ``project`` and ``holder`` when given, resuming after
        ``cursor``. A cursor no page minted raises :class:`~blizzard.hub.domain.kernel.pagination.MalformedCursor`."""
        ...


class ILockedPermitRead(Protocol):
    """The guard read of a locked writer, on the one transaction its row lock was taken on."""

    def permit(self) -> Permit:
        """The locked permit as it stands under the lock."""
        ...


class IWritePermitRepository(IReadPermitRepository, Protocol):
    """Adds the permit writes."""

    def create(self, permit: Permit) -> None:
        """Insert ``permit``. A project holds one permit per holder and capability, whatever its state: a second
        raises :class:`PermitAlreadyRequested`."""
        ...

    def locked(self, permit_id: str) -> AbstractContextManager[ILockedPermitRead]:
        """Lock the row of ``permit_id``, which exists, before any read, and yield the read handle on that
        transaction."""
        ...

    def record_fact_locked(self, held: ILockedPermitRead, fact: PermitFact) -> None:
        """Append ``fact`` on the transaction ``held`` was opened on."""
        ...


class PermitVerbRefused(Exception):
    """A verb was refused from the permit's current state."""

    def __init__(self, permit_id: str, *, verb: PermitVerb, state: PermitState) -> None:
        super().__init__(f"permit {permit_id} is {state} — {verb} refused")
        self.permit_id = permit_id
        self.verb = verb
        self.state = state


class RevocationNoteRequired(Exception):
    """A revocation carried a blank note."""

    def __init__(self, permit_id: str) -> None:
        super().__init__(f"revoking permit {permit_id} needs a note")
        self.permit_id = permit_id


class PermitAlreadyRequested(Exception):
    """A request repeated a holder and capability its project already holds a permit for."""

    def __init__(self, holder: str, *, capability: str) -> None:
        super().__init__(f"{holder} already has a permit for {capability!r} in this project")
        self.holder = holder
        self.capability = capability
