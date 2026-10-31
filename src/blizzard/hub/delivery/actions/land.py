"""Run each repository's landing policy in its project's landing order and combine their answers."""

from __future__ import annotations

from blizzard.hub.delivery.actions.landing.combine import (
    FAILURE,
    LANDED,
    combine,
    in_landing_order,
    refuses_before_checks,
)
from blizzard.hub.delivery.actions.landing.park import Park, latest_marker
from blizzard.hub.delivery.actions.landing.policy import POLICIES
from blizzard.hub.delivery.actions.landing.repo import (
    PENDING,
    Admitted,
    ForgeEscalation,
    Held,
    Landed,
    LandingFenced,
    LandingRun,
    NotRecorded,
    RepoLanding,
    RepoState,
    Waiting,
)
from blizzard.hub.domain.actions.effects import IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.config.landing_policies import LandingPolicy
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
            if RepositoryId(resolution.targets[index].record) in forges
        ]

    async def _land(self, run: LandingRun, forges: VisitForges) -> Pass:
        if not run.visit.git_commits.rows:
            run.say("no code to land: this chunk submitted no git commits")
            return run.answer(LANDED)
        already = run.visit.marker_names()
        no_code = run.visit.no_code_repos()
        unlanded = in_landing_order(
            repo for repo in self._repos(run, forges) if repo.marker() not in already and repo.repo not in no_code
        )
        if not unlanded:
            if no_code:
                run.say(f"no code to land: {', '.join(sorted(no_code))} already resolved")
            return run.answer(LANDED)
        first = unlanded[0]
        if run.visit.parked_watch() == RepositoryId(first.record.record):
            state = await Park().step(first, watch=True)
            if isinstance(state, Held):
                marker = latest_marker(first)
                assert marker is not None
                await run.post(f"park-reopen/{first.repo}/{marker.name.rsplit('/', 1)[1]}", "")
                return run.answer(PENDING)
            if not isinstance(state, Landed):
                return await combine(run, [(first, state)])
            await run.landed(first, state.revision)
            unlanded = unlanded[1:]
            if len(forges) < len(run.visit.resolution.targets):
                return run.answer(PENDING)
        gated = [
            (
                repo,
                Held()
                if repo.record.landing_policy is LandingPolicy.PARK
                else await POLICIES[repo.record.landing_policy].step(repo),
            )
            for repo in unlanded
        ]
        # A head refused anywhere fires nothing in any other repository.
        refused = any(refuses_before_checks(state) for _, state in gated)
        stepped: list[tuple[RepoLanding, RepoState]] = []
        for repo, state in gated:
            if isinstance(state, Admitted):
                state = Waiting("not stepped past its gate") if refused else await state.proceed()
            stepped.append((repo, state))
        return await combine(run, stepped)
