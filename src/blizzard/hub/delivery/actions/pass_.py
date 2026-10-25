"""``pass`` — the hub node that only routes: it does nothing and answers ``success``."""

from __future__ import annotations

from blizzard.hub.domain.actions.effects import IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.graph.actions import PASS
from blizzard.hub.forge.landing import VisitForges


class PassAction:
    spec = PASS

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        return Pass(choice="success")
