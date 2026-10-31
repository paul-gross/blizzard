"""``land`` — a runner of landing policies. Each pass steps every repository its visit resolved and has not yet
landed, in its project's landing order, through that repository's policy, then folds their states into the pass's one
answer (:mod:`~blizzard.hub.delivery.actions.landing.combine`). A pass skips what its node's markers say earlier
passes landed, and speaks to each repository only through its :class:`RepositoryForge`; a forge fault no worker can
repair escalates to a person."""

from __future__ import annotations

from blizzard.hub.delivery.actions.landing.combine import (
    FAILURE,
    LANDED,
    combine,
    in_landing_order,
    refuses_before_checks,
)
from blizzard.hub.delivery.actions.landing.policy import POLICIES
from blizzard.hub.delivery.actions.landing.repo import (
    PENDING,
    Admitted,
    ForgeEscalation,
    LandingFenced,
    LandingRun,
    NotRecorded,
    RepoLanding,
    RepoState,
    Waiting,
)
from blizzard.hub.domain.actions.effects import IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.graph.actions import LAND
from blizzard.hub.forge.landing import ForgeUnavailable, VisitForges


class Land:
    spec = LAND

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        run = LandingRun(visit, effects)
        try:
            return await self._land(run, forges)
        except NotRecorded as exc:
            run.say(f"marker write failed: {exc}")
            return run.answer(FAILURE)
        except LandingFenced as exc:
            run.say(f"the chunk moved on while it landed: {exc}; no other repository is touched")
            return run.answer(FAILURE)
        except ForgeEscalation as exc:
            run.say(f"escalated to a person: {exc}")
            return run.escalate(exc.escalation)
        except ForgeUnavailable as exc:
            run.hold(exc)
            run.say(f"the forge did not answer: {exc}; re-polling")
            return run.answer(PENDING)

    @staticmethod
    def _repos(run: LandingRun, forges: VisitForges) -> list[RepoLanding]:
        visit = run.visit
        rows, resolution = visit.git_commits.rows, visit.resolution
        return [
            RepoLanding(
                run=run,
                repo=resolution.qualified[index],
                branch=row.data.partition(":")[0],
                commit=row.data.partition(":")[2],
                record=resolution.targets[index],
                forge=forges[RepositoryId(resolution.targets[index].record)],
                forge_label=resolution.forge_of(resolution.targets[index]).label,
            )
            for index, row in enumerate(rows)
        ]

    async def _land(self, run: LandingRun, forges: VisitForges) -> Pass:
        if not run.visit.git_commits.rows and run.visit.node.expects_git_commits:
            run.say(
                "no git commits to deliver: this chunk submitted no git_commit artifact, so there is nothing to "
                "open a PR for. A delivery node reached with an empty commit set is a failure, not a landing — "
                "check that the nodes before this one declared their commits (`blizzard runner artifact "
                "commit`) and that each verified."
            )
            return run.answer(FAILURE)
        already = run.visit.marker_names()
        unlanded = in_landing_order(repo for repo in self._repos(run, forges) if repo.marker() not in already)
        if not unlanded:
            return run.answer(LANDED)
        gated = [(repo, await POLICIES[repo.record.landing_policy].step(repo)) for repo in unlanded]
        # A head refused anywhere fires nothing in any other repository.
        refused = any(refuses_before_checks(state) for _, state in gated)
        stepped: list[tuple[RepoLanding, RepoState]] = []
        for repo, state in gated:
            if isinstance(state, Admitted):
                state = Waiting("not stepped past its gate") if refused else await state.proceed()
            stepped.append((repo, state))
        return await combine(run, stepped)
