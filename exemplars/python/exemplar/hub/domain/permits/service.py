"""Permit use cases — request a permit, then move it through its verbs under the permit's row lock."""

# exemplar: bzh:domain-orchestration-split, bzh:narrow-seams, bzh:store-exclusive-write, bzh:domain-takes-objects.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.projects.model import Project
from exemplar.hub.domain.permits.model import IWritePermitRepository, Permit, PermitFact

_PERMIT_PREFIX = "pm"  # exemplar: a real concept declares its prefix as an ``IdPrefix`` member.

_log = get_logger("blizzard.hub.permits")


class PermitService:
    """Request permits and record the facts their verbs decide."""

    def __init__(self, *, permits: IWritePermitRepository, clock: IClock) -> None:
        self._permits = permits
        self._clock = clock

    def request(self, *, project: Project, holder: str, capability: str, by: str) -> Permit:
        """A freshly requested permit in ``project``, for ``holder`` to use ``capability``."""
        now = self._clock.now()
        permit_id = Id.mint_at(_PERMIT_PREFIX, now).value
        permit = Permit.request(
            permit_id, project_id=project.project_id, holder=holder, capability=capability, by=by, at=now
        )
        self._permits.create(permit)
        _log.info(
            "permit requested",
            permit_id=permit_id,
            project_id=project.project_id,
            holder=holder,
            capability=capability,
            by=by,
        )
        return permit

    def grant(self, permit: Permit, *, by: str) -> Permit:
        """Grant a requested permit."""
        return self._decide(permit, lambda current, at: current.grant(by=by, at=at))

    def suspend(self, permit: Permit, *, by: str) -> Permit:
        """Suspend a granted permit."""
        return self._decide(permit, lambda current, at: current.suspend(by=by, at=at))

    def resume(self, permit: Permit, *, by: str) -> Permit:
        """Resume a suspended permit."""
        return self._decide(permit, lambda current, at: current.resume(by=by, at=at))

    def revoke(self, permit: Permit, *, note: str, by: str) -> Permit:
        """Revoke a live permit, recording ``note``."""
        return self._decide(permit, lambda current, at: current.revoke(note=note, by=by, at=at))

    def _decide(self, permit: Permit, verb: Callable[[Permit, datetime], PermitFact]) -> Permit:
        with self._permits.locked(permit.permit_id) as held:
            current = held.permit()
            fact = verb(current, self._clock.now())
            self._permits.record_fact_locked(held, fact)
        _log.info("permit fact recorded", permit_id=fact.permit_id, kind=fact.kind, by=fact.by)
        return replace(current, latest=fact)
