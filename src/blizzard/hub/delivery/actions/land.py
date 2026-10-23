"""``land`` — lands a chunk's commits on each repository's base branch.

Its forge stages are not yet ported here: a pass answers ``failure``, naming why, so a node that names
``land`` routes rather than waits. Graphs land through the ``land_pr_ci`` ``run:`` step meanwhile."""

from __future__ import annotations

from blizzard.hub.delivery.forge_handle import VisitForges
from blizzard.hub.domain.actions.effects import IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.graph.actions import LAND


class Land:
    spec = LAND

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        return Pass(choice="failure", log=("land: no forge stages are available to this pass",))
