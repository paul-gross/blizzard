"""``garden-deliver`` — deliver a routine run's ``delta`` and ``proposals`` artifacts, each a list that may
be empty, and answer from what the hub made of them: ``recorded``, ``invalid`` with the recorder's
explanation in the ``garden-delivery-failure`` marker its edge reads back, or ``failure`` when the chunk
has moved past the visit."""

from __future__ import annotations

from blizzard.hub.delivery.actions.delivery_outcome import answer_delivery
from blizzard.hub.domain.actions.effects import IEffectSink, Pass, RecordGardenDelivery
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.graph.actions import GARDEN_DELIVER
from blizzard.hub.forge.landing import VisitForges

_FAILURE_MARKER_NAME = "garden-delivery-failure"


class GardenDeliver:
    spec = GARDEN_DELIVER

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        args = visit.node.action_args
        result = await effects.record(
            RecordGardenDelivery(delta=args.get("delta", ()), proposals=args.get("proposals", ()))
        )
        return await answer_delivery(
            result, effects, failure_marker_name=_FAILURE_MARKER_NAME, action="garden delivery"
        )
