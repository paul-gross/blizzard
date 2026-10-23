"""Giving a gate-parked chunk back to the queue — whether a runner offers, and the offer itself.

A runner set to ``release`` asks the hub to take the route back, and the chunk re-derives ``ready`` at its current node.
A refusal, an older hub that does not know the route, and an unreachable hub all leave the route held, as ``hold``
would. Only the hub's acceptance runs the caller's letting go of what it holds locally; no outcome writes a fact."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.client import HubClientError
from blizzard.runner.throttle.pause import USAGE_LIMIT_PARK_CAUSE, PausePark
from blizzard.runner.throttle.settings import HoldOrRelease, ThrottleSettings

__all__ = [
    "IReleaseHub",
    "Release",
    "ReleaseContext",
    "ReleaseReason",
    "releases_between_steps",
    "releases_on_decline",
    "releases_usage_limited",
]

_log = get_logger("blizzard.runner.loop")

# Hub took the route back, environments still bound: InterruptedClaims holds the binding, then reclaims or lets it go.
_CP_AFTER_HUB_BEFORE_ENVIRONMENTS = crashpoint(
    "release.after-hub.before-environments",
    "hub accepted the release between steps; the chunk's environments are still bound",
)
# Hub took the route back, usage booked, the parked lease still open: the reconcile sweep's abandon closes it.
_CP_USAGE_LIMITED_AFTER_HUB_BEFORE_ABANDON = crashpoint(
    "release.usage-limited.after-hub.before-abandon",
    "hub accepted the release of a usage-limited lease; the lease is not yet abandoned",
)


class ReleaseReason(StrEnum):
    """Why the runner lets go — recorded by the hub as given, never interpreted."""

    #: The gate declined the chunk's next step.
    THROTTLED = "throttled"
    #: A usage limit parked the chunk's step part-way, and the floor has not reopened.
    USAGE_LIMITED = "usage-limited"


def releases_on_decline(settings: ThrottleSettings | None) -> bool:
    """Whether the runner is set to give a chunk back when its gate declines the chunk's next step."""
    return _choices(settings).on_decline is HoldOrRelease.RELEASE


def releases_between_steps(settings: ThrottleSettings | None, *, declined: bool) -> bool:
    """Whether a chunk entering its next step is offered back: only when the gate itself — the floor or a lever, never
    a brake or the chunk's own pause — ``declined`` the step, and the runner is set to release on a decline."""
    return declined and releases_on_decline(settings)


def releases_usage_limited(
    settings: ThrottleSettings | None, park: PausePark, *, operator_holds: bool, declined: bool
) -> bool:
    """Whether a parked lease is offered back: only a park a usage limit wrote, on a chunk no operator holds (its pause,
    an open ask), whose floor still ``declined`` the continuation, on a runner set to release on a usage limit. A park
    whose floor has opened is woken instead."""
    return (
        park.cause == USAGE_LIMIT_PARK_CAUSE
        and not operator_holds
        and declined
        and _choices(settings).on_usage_limit is HoldOrRelease.RELEASE
    )


def _choices(settings: ThrottleSettings | None) -> ThrottleSettings:
    return settings if settings is not None else ThrottleSettings()


class IReleaseHub(Protocol):
    def release(self, chunk_id: str, *, epoch: int, route_token: str | None, reason: str) -> bool:
        """True only when the hub gave the route back; a transport error raises :class:`HubClientError`."""
        ...


class ReleaseContext(Protocol):
    """What an offer reads."""

    @property
    def hub(self) -> IReleaseHub: ...


@dataclass(frozen=True)
class Release:
    """One offer of a held chunk back to the hub."""

    ctx: ReleaseContext

    def offer(
        self,
        chunk_id: str,
        *,
        epoch: int,
        route_token: str | None,
        reason: ReleaseReason,
        then: Callable[[], None],
    ) -> bool:
        """Ask the hub to take ``chunk_id``'s route back; only on its acceptance run ``then``, the caller's letting go.
        True when it let go. Any refusal, and an unreachable hub, leaves everything as it was."""
        try:
            accepted = self.ctx.hub.release(chunk_id, epoch=epoch, route_token=route_token, reason=reason.value)
        except HubClientError as exc:
            _log.info("release not offered — hub unreachable, holding", chunk_id=chunk_id, detail=str(exc))
            return False
        if not accepted:
            _log.info("release refused — holding", chunk_id=chunk_id, reason=reason.value)
            return False
        if reason is ReleaseReason.USAGE_LIMITED:
            _CP_USAGE_LIMITED_AFTER_HUB_BEFORE_ABANDON.reached()
        else:
            _CP_AFTER_HUB_BEFORE_ENVIRONMENTS.reached()
        then()
        _log.info("released chunk back to the queue", chunk_id=chunk_id, epoch=epoch, reason=reason.value)
        return True
