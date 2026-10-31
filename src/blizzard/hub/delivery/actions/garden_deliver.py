"""``garden-deliver`` — deliver a routine run's ``delta`` and ``proposals`` artifacts, each a list that may
be empty, and answer from what the hub made of them
(``src/blizzard/hub/delivery/actions/delivery_outcome.py::answer_delivery``)."""

from __future__ import annotations

from blizzard.hub.delivery.actions.delivery_outcome import answer_delivery
from blizzard.hub.domain.actions.effects import IEffectSink, Pass, RecordGardenDelivery
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.graph.actions import GARDEN_DELIVER
from blizzard.hub.domain.kernel.garden_delivery_fault import GardenDeliveryFault
from blizzard.hub.forge.landing import VisitForges

_FAILURE_MARKER_NAME = "garden-delivery-failure"
_DELTA_INVALID = "invalid"
_PROPOSALS_INVALID = "invalid-proposals"


class GardenDeliver:
    spec = GARDEN_DELIVER

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        args = visit.node.action_args
        result = await effects.record(
            RecordGardenDelivery(delta=args.get("delta", ()), proposals=args.get("proposals", ()))
        )
        # A fault in the docket (or a citation spanning both artifacts) goes back to the node that drafts it.
        invalid_choice = _PROPOSALS_INVALID if result.refused == GardenDeliveryFault.PROPOSALS else _DELTA_INVALID
        return await answer_delivery(
            result,
            effects,
            failure_marker_name=_FAILURE_MARKER_NAME,
            action="garden delivery",
            invalid_choice=invalid_choice,
        )
