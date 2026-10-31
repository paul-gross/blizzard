"""The order a chunk's repositories land in, and the one answer their states fold into.

Repositories sort by the landing position their visit took at its first turn — :func:`landing_sort_key`, the one
place the rule is written. The fold answers, in priority: a person's call; a foreign head's ``failure``; a
``conflict``; the repositories' own check ``failure``; ``inherited-failure``; ``pending`` while any repository is
not yet prepared. Only once every one is prepared does it land them, one at a time in order, each landing handed
over before the next is taken — so a refusal after a landing leaves the landed repository standing."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from blizzard.hub.delivery.actions.landing.checks import INHERITED_FAILURE, Findings
from blizzard.hub.delivery.actions.landing.fast_forward import cleanup
from blizzard.hub.delivery.actions.landing.park import Park
from blizzard.hub.delivery.actions.landing.repo import (
    PENDING,
    Admitted,
    AwaitingMerge,
    Conflict,
    Failure,
    Held,
    Inherited,
    Landed,
    LandingRun,
    NeedsPerson,
    NotRecorded,
    Ready,
    RepoLanding,
    RepoState,
    Waiting,
)
from blizzard.hub.domain.actions.effects import Pass
from blizzard.hub.domain.config.landing_policies import LandingPolicy, landing_sort_key

LANDED = "landed"
CONFLICT = "conflict"
FAILURE = "failure"

# The marker a terminal failure or a substantive wait writes its findings under.
_FINDINGS_NAME = "delivery-findings"
_FOREIGN_FINDINGS_NAME = f"{_FINDINGS_NAME}/foreign-head"


def in_landing_order[T: RepoLanding](repos: Iterable[T]) -> list[T]:
    """``repos`` in their project's landing order, as their visit took it; the sort is stable, so repositories
    the key cannot tell apart keep the order they came in."""
    return sorted(repos, key=_landing_key)


def _landing_key(repo: RepoLanding) -> tuple[bool, float, str]:
    return landing_sort_key(repo.record.landing_position, repo.record.slug)


def refuses_before_checks(state: RepoState | Admitted) -> bool:
    """Whether ``state``, answered before any repository's checks are read, decides the pass alone: a person's call
    or a foreign head, which outrank everything later and must fire nothing in any other repository."""
    return isinstance(state, NeedsPerson) or (isinstance(state, Failure) and state.foreign_head)


async def combine(run: LandingRun, stepped: Sequence[tuple[RepoLanding, RepoState]]) -> Pass:
    """Fold each unlanded repository's state into the pass's answer, landing every ready one in order once all
    are prepared. A parked state — :class:`Held` or :class:`AwaitingMerge` — is refused: no policy that produces
    one is run."""
    ordered = sorted(stepped, key=lambda pair: _landing_key(pair[0]))
    states = [state for _, state in ordered]
    for repo, state in ordered:
        if isinstance(state, Landed):
            await run.landed(repo, state.revision)
            if repo.record.landing_policy is LandingPolicy.FAST_FORWARD:
                await cleanup(repo, state.revision)

    if person := next((state for state in states if isinstance(state, NeedsPerson)), None):
        run.say(f"escalated to a person: {person.escalation.detail}")
        return run.escalate(person.escalation)
    if foreign := _findings(state for state in states if isinstance(state, Failure) and state.foreign_head):
        # Unguarded: unwritten findings leave the repair worker nothing to read.
        rendered = Findings(foreign).render()
        await run.post(_FINDINGS_NAME, rendered)
        await run.post(_FOREIGN_FINDINGS_NAME, rendered)
        return run.answer(FAILURE)
    if conflict := next((state for state in states if isinstance(state, Conflict)), None):
        run.say(f"conflict: {conflict.detail}")
        return run.answer(CONFLICT)
    if failures := _findings(state for state in states if isinstance(state, Failure)):
        # Nothing lands (chunk atomicity); unguarded, since unwritten findings leave `resolve` nothing.
        await run.post(_FINDINGS_NAME, Findings(failures).render())
        return run.answer(FAILURE)
    if inherited := _findings(state for state in states if isinstance(state, Inherited)):
        # Nothing lands, and unguarded as above: the repair charge reads these findings.
        await run.post(_FINDINGS_NAME, Findings(inherited).render())
        return run.answer(INHERITED_FAILURE)
    if waits := [state for state in states if isinstance(state, Waiting)]:
        # Land nothing. Findings are re-written every poll, so a refused write here only waits.
        if records := tuple(state.findings for state in waits if state.findings is not None):
            try:
                await run.post(_FINDINGS_NAME, Findings(records).render())
            except NotRecorded as exc:
                run.say(f"delivery-findings write failed (wait path, non-fatal): {exc}")
        return run.answer(PENDING)

    if any(isinstance(state, AwaitingMerge) for state in states):
        return run.answer("awaiting-merge")

    # Every repository is prepared: land each in order, each durable before the next is touched.
    for repo, state in ordered:
        if isinstance(state, Held):
            parked = await Park().step(repo)
            if isinstance(parked, AwaitingMerge):
                return run.answer("awaiting-merge")
            if isinstance(parked, NeedsPerson):
                return run.escalate(parked.escalation)
            if isinstance(parked, Landed):
                await run.landed(repo, parked.revision)
                continue
            return run.answer(PENDING)
        if not isinstance(state, Ready):
            continue
        outcome = await state.land()
        if isinstance(outcome, Waiting):
            run.say(outcome.reason)
            return run.answer(PENDING)
        if isinstance(outcome, NeedsPerson):
            return run.escalate(outcome.escalation)
        await run.landed(repo, outcome.revision)
        if repo.record.landing_policy is LandingPolicy.FAST_FORWARD:
            await cleanup(repo, outcome.revision)
    return run.answer(LANDED)


def _findings(states: Iterable[Failure | Inherited]) -> tuple[dict[str, Any], ...]:
    return tuple(record for state in states for record in state.findings)
