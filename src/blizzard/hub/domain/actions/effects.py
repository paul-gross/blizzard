"""What crosses between a pass and the hub: the answer a pass gives, the effects it records, and the sink
it records them through. An action never writes; it hands each effect to the sink and waits for it to be
durable (``bzh:hub-action-purity``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.graph.actions import RESERVED_WAITS


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
    :attr:`EffectStatus.RECORDED`."""

    status: EffectStatus
    detail: str = ""


@domain_model
@dataclass(frozen=True)
class ActionEscalation:
    """Something only a person can settle: the cause the chunk escalates with, and what it is about."""

    cause: EscalationCause
    detail: str


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
