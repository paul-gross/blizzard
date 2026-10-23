"""``record-findings`` — deliver the chunk's newest review-finding delta, and answer from what the hub made
of it: ``recorded``, ``invalid`` with the recorder's explanation in the ``review-findings-failure`` marker
its edge reads back, or ``failure`` when the chunk has moved past the visit."""

from __future__ import annotations

from blizzard.hub.delivery.actions.delivery_outcome import answer_delivery
from blizzard.hub.delivery.forge_handle import VisitForges
from blizzard.hub.domain.actions.effects import IEffectSink, Pass, RecordReviewFindings
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.graph.actions import RECORD_FINDINGS

_FAILURE_MARKER_NAME = "review-findings-failure"


class RecordFindings:
    spec = RECORD_FINDINGS

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        result = await effects.record(RecordReviewFindings())
        return await answer_delivery(
            result, effects, failure_marker_name=_FAILURE_MARKER_NAME, action="review findings delivery"
        )
