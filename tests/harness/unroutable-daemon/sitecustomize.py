"""A deliberately incompatible action implementation, loaded only by the disposable hub.

The catalogue still advertises the graph's authored answer, as it would for a
carried-over graph after an action implementation changes. The daemon's real
router must handle the unexpected answer without a corrupt store.
"""

from blizzard.hub.delivery.actions.test_actions import AnswerAction
from blizzard.hub.domain.actions.effects import IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.forge.landing import VisitForges


async def answer_unadvertised(self: AnswerAction, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
    return Pass(choice="unadvertised")


AnswerAction.take = answer_unadvertised
