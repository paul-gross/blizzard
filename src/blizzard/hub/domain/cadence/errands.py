"""A cadence's errands and when each is due.

Pure values and functions: a chore's scopes resolve to its errands against the routines and scopes as they stand,
an errand's last run is read through its work item and its holder's facts, and the due rule is a function of the
rhythm, the errand, its last run, and one handed instant ``now``. None reads a clock; cron is reached only through
:class:`~blizzard.hub.domain.cadence.schedule.Schedule`."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from blizzard.foundation.cadence_attention import ErrandState
from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.roles import domain_model
from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.cadence.model import (
    Anchor,
    Attention,
    AttentionFacts,
    Cadence,
    Chore,
    Rhythm,
    ScopeSelection,
    live_swept_scopes,
)
from blizzard.hub.domain.chunk.model import ChunkFacts
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.scopes import Scope


@domain_model
@dataclass(frozen=True)
class ErrandKey:
    """What identifies an errand: its chore's id and its scope's id — the key its runs' marks carry."""

    chore_id: str
    scope_id: str


@domain_model
@dataclass(frozen=True)
class Errand:
    """One chore over one scope, worked out each time it is read, never stored. ``joined_at`` is the instant it
    joined its chore, which a never-run errand counts from."""

    cadence_id: str
    chore: Chore
    scope_id: str
    scope_slug: str
    joined_at: datetime

    @property
    def key(self) -> ErrandKey:
        return ErrandKey(self.chore.chore_id, self.scope_id)

    def stopped_by(self, found: Collection[Attention]) -> bool:
        """Whether any of ``found`` stops this errand: one naming the whole cadence, its chore outright, or its
        chore over this errand's scope."""
        return any(
            item.chore is None or (item.chore == self.chore.key and item.scope in (None, self.scope_slug))
            for item in found
        )


def _joined_at(chore: Chore, routine: Routine, scope: Scope, facts: AttentionFacts) -> datetime:
    """The later of when the chore last set its scopes and, for an ``all`` chore, when the scope was linked; an
    unstamped link reads as linked before the chore."""
    if chore.spec.selection is not ScopeSelection.ALL:
        return chore.scopes_set_at
    linked_at = facts.linked_at.get(routine.routine_id, {}).get(scope.slug)
    return chore.scopes_set_at if linked_at is None else max(chore.scopes_set_at, linked_at)


def _chore_scopes(chore: Chore, routine: Routine, facts: AttentionFacts) -> tuple[Scope, ...]:
    if chore.spec.selection is ScopeSelection.ALL:
        return live_swept_scopes(routine, facts)
    slugs = chore.spec.scopes if chore.spec.selection is ScopeSelection.LISTED else (routine.default_scope_slug,)
    return tuple(scope for slug in slugs if (scope := facts.scopes.get(slug)) is not None)


def chore_errands(cadence_id: str, chore: Chore, facts: AttentionFacts) -> tuple[Errand, ...]:
    """``chore``'s errands: for ``all``, one per scope its routine sweeps that is not retired; for a list, one per
    listed scope the project knows; absent, one over the routine's default scope. None when its routine is
    unknown. A listed scope that is retired or unlinked is still an errand — attention stops it."""
    routine = facts.routines.get(chore.spec.routine_slug)
    if routine is None:
        return ()
    return tuple(
        Errand(cadence_id, chore, scope.scope_id, scope.slug, _joined_at(chore, routine, scope, facts))
        for scope in _chore_scopes(chore, routine, facts)
    )


def errands(cadence: Cadence, facts: AttentionFacts) -> tuple[Errand, ...]:
    """Every errand of ``cadence``'s chores, in chore order, read against ``facts``."""
    return tuple(errand for chore in cadence.chores for errand in chore_errands(cadence.cadence_id, chore, facts))


# --- the last run ---


@domain_model
@dataclass(frozen=True)
class Holder:
    """The facts read from the live chunk holding a run's work item — a grouped run's survivor, whichever chunk
    that now is: its chunk, its status, its first lease's mint, and the instant it became terminal."""

    status: ChunkStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    chunk_id: str | None = None


class RunOutcome(StrEnum):
    """How an errand's last run stands: still live, finished (done or delivered), or cancelled (stopped or
    withdrawn). A run at ``needs_human`` is live — a failure is waited on, never quietly retried."""

    LIVE = "live"
    FINISHED = "finished"
    CANCELLED = "cancelled"


@domain_model
@dataclass(frozen=True)
class LastRun:
    """An errand's newest marked work item, by minted instant, with its closure and its holder's facts.
    ``holder`` is ``None`` when the holder could not be resolved, which reads as live, never as never-run."""

    minted_at: datetime
    closure: WorkItemClosure | None = None
    closed_at: datetime | None = None
    holder: Holder | None = None

    def outcome(self) -> RunOutcome:
        """Withdrawn first, then delivered, then the holder's status; an unresolvable holder is live."""
        if self.closure is WorkItemClosure.WITHDRAWN:
            return RunOutcome.CANCELLED
        if self.closure is WorkItemClosure.DELIVERED:
            return RunOutcome.FINISHED
        if self.holder is None:
            return RunOutcome.LIVE
        if self.holder.status is ChunkStatus.DONE:
            return RunOutcome.FINISHED
        if self.holder.status is ChunkStatus.STOPPED:
            return RunOutcome.CANCELLED
        return RunOutcome.LIVE

    def unfinished(self) -> bool:
        return self.outcome() is RunOutcome.LIVE

    def anchored_at(self, anchor: Anchor) -> datetime | None:
        """The instant ``anchor`` reads on this run. ``started`` reads ``scheduled`` for a run settled before any
        runner took it up; ``finished`` is the earlier of the holder's completion and the item's closure, and is
        ``None`` only while the run is live."""
        if anchor is Anchor.SCHEDULED:
            return self.minted_at
        if anchor is Anchor.STARTED:
            started = self.holder.started_at if self.holder is not None else None
            return started or self.minted_at
        if self.unfinished():
            return None
        terminal_holder = self.holder is not None and self.holder.status in (ChunkStatus.DONE, ChunkStatus.STOPPED)
        candidates: Sequence[datetime | None] = (
            self.closed_at,
            self.holder.completed_at if terminal_holder and self.holder is not None else None,
        )
        return min((at for at in candidates if at is not None), default=None)


@domain_model
@dataclass(frozen=True)
class ErrandRun:
    """An errand's last run as the store reads it: the marked work item's id beside its reading, so a re-read can
    tell whether the newest run is still the one a pass judged."""

    work_item_id: str
    run: LastRun


def holder_of(held: Mapping[str, ChunkFacts]) -> Holder | None:
    """The holder of a run's work item among ``held``, the non-ephemeral chunks holding its ref by chunk id: the
    newest live one, else the newest terminal one, by chunk id. ``None`` when none holds it, which the run reads
    as live."""
    if not held:
        return None
    live = [chunk_id for chunk_id, facts in held.items() if facts.status() not in TERMINAL_STATUSES]
    chunk_id = max(live or held)
    facts = held[chunk_id]
    return Holder(
        status=facts.status(), started_at=facts.started_at(), completed_at=facts.completed_at(), chunk_id=chunk_id
    )


# --- the due rule ---


def eligible_at(rhythm: Rhythm, last_run: LastRun | None) -> datetime | None:
    """The instant the spacing lets the errand come due — the anchor's instant plus the spacing — or ``None`` when
    there is no spacing, no last run to count from, or the anchor has no instant yet."""
    anchor = rhythm.effective_anchor()
    if rhythm.spacing is None or anchor is None or last_run is None:
        return None
    anchored = last_run.anchored_at(anchor)
    return None if anchored is None else anchored + timedelta(minutes=rhythm.spacing.minutes)


def is_due(rhythm: Rhythm, errand: Errand, last_run: LastRun | None, now: datetime) -> bool:
    """Whether ``errand`` is due at ``now``. An unfinished last run holds it. Otherwise a spacing alone is due at
    or past eligibility — at once with no last run; a schedule alone when its latest moment at or before ``now``
    falls after the reference (the last run's minted instant, or the instant a never-run errand joined); both when
    that moment falls after the reference and at or past eligibility. A moment missed is owed one run, however
    many fell since the reference."""
    if last_run is not None and last_run.unfinished():
        return False
    eligible = eligible_at(rhythm, last_run)
    if rhythm.schedule is None:
        if eligible is None:
            return last_run is not None or now >= errand.joined_at
        return now >= eligible
    moment = rhythm.schedule.latest_at_or_before(now)
    if moment is None:
        return False
    reference = errand.joined_at if last_run is None else last_run.minted_at
    return moment > reference and (eligible is None or moment >= eligible)


def due_at(rhythm: Rhythm, errand: Errand, last_run: LastRun | None) -> datetime | None:
    """The first instant :func:`is_due` holds given ``last_run``, or ``None`` while the last run is live. A spacing
    alone is due at eligibility — for a never-run errand, the instant it joined; a schedule alone at the first
    moment after the reference; both at the first moment after the reference and at or after eligibility. It may
    lie in the past: a moment missed is still owed."""
    if last_run is not None and last_run.unfinished():
        return None
    eligible = eligible_at(rhythm, last_run)
    reference = errand.joined_at if last_run is None else last_run.minted_at
    if rhythm.schedule is None:
        return errand.joined_at if eligible is None else eligible
    moment = rhythm.schedule.first_after(reference)
    if moment is not None and eligible is not None and moment < eligible:
        return rhythm.schedule.first_at_or_after(eligible)
    return moment


def errand_state(
    rhythm: Rhythm,
    errand: Errand,
    last_run: LastRun | None,
    *,
    stopping: Collection[Attention],
    retired: bool,
    now: datetime,
) -> ErrandState:
    """Where ``errand`` stands at ``now``. A retired cadence, or attention stopping the errand, reads ``stopped``
    even while a run is live; then an unfinished last run reads ``running``, a due errand ``due``, else
    ``waiting``. ``due`` is exactly what the sweep's selection would judge due (:meth:`Errand.stopped_by` and
    :func:`is_due`)."""
    if retired or errand.stopped_by(stopping):
        return ErrandState.STOPPED
    if last_run is not None and last_run.unfinished():
        return ErrandState.RUNNING
    return ErrandState.DUE if is_due(rhythm, errand, last_run, now) else ErrandState.WAITING
