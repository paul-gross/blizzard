"""``record-findings`` — deliver the chunk's newest review-finding delta, and answer from what the hub made
of it (``src/blizzard/hub/delivery/actions/delivery_outcome.py::answer_delivery``)."""

from __future__ import annotations

from blizzard.hub.delivery.actions.delivery_outcome import answer_delivery
from blizzard.hub.domain.actions.effects import IEffectSink, Pass, RecordReviewFindings
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.graph.actions import RECORD_FINDINGS
from blizzard.hub.forge.landing import VisitForges

_FAILURE_MARKER_NAME = "review-findings-failure"


class RecordFindings:
    spec = RECORD_FINDINGS

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        result = await effects.record(RecordReviewFindings())
        return await answer_delivery(
            result, effects, failure_marker_name=_FAILURE_MARKER_NAME, action="review findings delivery"
        )
