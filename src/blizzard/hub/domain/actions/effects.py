"""What crosses between a pass and the hub: the answer a pass gives, the effects it records, and the sink
it records them through. An action never writes; it hands each effect to the sink and waits for it to be
durable (``bzh:hub-action-purity``)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.graph.actions import RESERVED_WAITS
from blizzard.hub.domain.kernel.forge_refusals import AuthRefusal, ClassifiedRefusal, ForgeDiagnostic


@domain_model
@dataclass(frozen=True)
class Marker:
    """A marker artifact recorded at the visit's node, idempotent per ``(chunk, node, name, epoch)``."""

    name: str
    content: str


@domain_model
@dataclass(frozen=True)
class LandingRecord:
    """A merge that has happened: ``repository`` is the record id the visit resolved, ``repo`` the
    ``owner/repo`` the ``merged/<repo>`` marker is named for, and ``revision`` the landed commit."""

    repository: str
    repo: str
    revision: str


@domain_model
@dataclass(frozen=True)
class RecordReviewFindings:
    """Deliver the chunk's newest review-finding delta."""


@domain_model
@dataclass(frozen=True)
class RecordGardenDelivery:
    """Deliver a routine run's named delta and proposal artifacts."""

    delta: tuple[str, ...]
    proposals: tuple[str, ...]


type Effect = Marker | LandingRecord | RecordReviewFindings | RecordGardenDelivery


class EffectStatus(StrEnum):
    RECORDED = "recorded"
    # The service refused the record on its merits; nothing was written.
    INVALID = "invalid"
    # The chunk has moved past the visit; nothing was written, a landing record aside.
    FENCED = "fenced"


@domain_model
@dataclass(frozen=True)
class EffectResult:
    """How the hub took one effect. ``detail`` is the service's or the fence's own explanation, empty on
    :attr:`EffectStatus.RECORDED`; ``refused`` is the part of the record that was refused, when the
    service says."""

    status: EffectStatus
    detail: str = ""
    #: On :attr:`EffectStatus.INVALID`, which part of the record the service refused; empty when it names none.
    refused: str = ""


@domain_model
@dataclass(frozen=True)
class MissingCapability:
    """A forge capability a pass found missing mid-pass: the repository record whose forge lacks it, and every
    capability it lacks — nonempty, deduplicated, in :class:`Capability` value order."""

    repository: RepositoryId
    capabilities: tuple[Capability, ...]

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise ValueError("a missing-capability reason names at least one capability")
        if list(self.capabilities) != sorted(set(self.capabilities)):
            raise ValueError("a missing-capability reason's capabilities are deduplicated and ordered by value")

    @classmethod
    def of(cls, repository: RepositoryId, missing: Iterable[Capability]) -> MissingCapability:
        return cls(repository, tuple(sorted(set(missing))))

    @property
    def first(self) -> Capability:
        return self.capabilities[0]


@domain_model
@dataclass(frozen=True)
class ForgeAuthReason:
    """A credential the forge refused mid-pass: why, the forge's sanitized answer, and the repository record and
    forge the refused call was made to."""

    refusal: AuthRefusal
    diagnostic: ForgeDiagnostic
    repository: RepositoryId
    forge: ForgeId

    @property
    def classified(self) -> ClassifiedRefusal:
        return ClassifiedRefusal(self.refusal, self.diagnostic)


@domain_model
@dataclass(frozen=True)
class ActionEscalation:
    """Something only a person can settle: the cause the chunk escalates with, and what it is about.
    ``missing`` is the typed reason of a :attr:`EscalationCause.FORGE_CAPABILITY_MISSING` escalation, and only of it;
    ``auth`` that of a :attr:`EscalationCause.FORGE_AUTH` escalation, and only of it."""

    cause: EscalationCause
    detail: str
    missing: MissingCapability | None = None
    auth: ForgeAuthReason | None = None

    def __post_init__(self) -> None:
        if (self.cause is EscalationCause.FORGE_CAPABILITY_MISSING) != (self.missing is not None):
            raise ValueError("a missing-capability reason goes with the forge-capability-missing cause, and only it")
        if self.auth is not None and self.cause is not EscalationCause.FORGE_AUTH:
            raise ValueError("a forge-auth reason goes with the forge-auth cause, and only it")


@domain_model
@dataclass(frozen=True)
class Pass:
    """A pass's answer: a choice from its action's answers, or an escalation in place of one. ``log`` holds
    the lines the visit's ``hub-log.<action>`` artifact carries; ``next_poll_at``, on a reserved wait, is the
    earliest moment the action wants to be asked again, and only ever postpones."""

    choice: str | None
    log: tuple[str, ...] = ()
    next_poll_at: datetime | None = None
    escalation: ActionEscalation | None = None

    def waits(self) -> bool:
        """Whether the answer is a reserved wait, ending the pass without moving the chunk."""
        return self.choice in RESERVED_WAITS


class IEffectSink(Protocol):
    """How an action records anything."""

    async def record(self, effect: Effect) -> EffectResult:
        """Return once the hub has made ``effect`` durable, or refused it."""
        ...
