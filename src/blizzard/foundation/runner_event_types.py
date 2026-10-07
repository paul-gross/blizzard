"""The runner's SSE frame vocabulary, its frame kinds and the causes a frame names —
one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from blizzard.foundation.leases import LeaseClosureReason


class RunnerEventType(StrEnum):
    """Every runner SSE frame kind — the ``event:`` name a frame carries."""

    LEASE_CHANGED = "lease-changed"
    ASK_CHANGED = "ask-changed"
    ESCALATION_CHANGED = "escalation-changed"
    TAKEOVER_CHANGED = "takeover-changed"
    ENVIRONMENT_CHANGED = "environment-changed"
    FACT_CHANGED = "fact-changed"


#: What caused a ``lease-changed`` frame: ``created``/``spawned`` are not closures, ``dormant`` is
#: an open-lease park; the other seven are :class:`~blizzard.foundation.leases.LeaseClosureReason`'s members.
LeaseChangeCause = Literal[
    "created",
    "spawned",
    "dormant",
    LeaseClosureReason.TRANSITIONED,
    LeaseClosureReason.REAPED,
    LeaseClosureReason.FAILED,
    LeaseClosureReason.ESCALATED,
    LeaseClosureReason.PARKED,
    LeaseClosureReason.RELEASED,
    LeaseClosureReason.PREEMPTED,
]

#: What caused an ``ask-changed`` frame — a worker's question recorded, or its answer
#: landing (the park resume the answer drives).
AskChangeCause = Literal["asked", "answered"]

#: What caused an ``escalation-changed`` frame — opened at an exhausted retry budget, or
#: closed by supersession (a fresh lease minted, or the hub resolving it terminally).
EscalationChangeCause = Literal["opened", "closed"]

#: What caused a ``takeover-changed`` frame.
TakeoverChangeCause = Literal["opened", "closed"]

#: What caused an ``environment-changed`` frame.
EnvironmentChangeCause = Literal["bound", "released"]
