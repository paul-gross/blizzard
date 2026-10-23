"""The two actions a hub process driven by the end-to-end suites and the crash sweep may name, admitted to
its catalogue only behind both test fences (``src/blizzard/hub/delivery/action_registry.py``)."""

from __future__ import annotations

from blizzard.hub.delivery.forge_handle import VisitForges
from blizzard.hub.domain.actions.effects import IEffectSink, Marker, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.graph.actions import TEST_ANSWER, TEST_SEQUENCE

_SEQUENCE_MARKER_PREFIX = "test-sequence/"


class AnswerAction:
    """``test.answer`` — answers its ``choice``."""

    spec = TEST_ANSWER

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        return Pass(choice=visit.node.action_args["choice"][0])


class SequenceAction:
    """``test.sequence`` — answers each of its ``choices`` in turn, one per pass, counting its place by the
    ``test-sequence/<n>`` markers earlier passes recorded; past the end it keeps answering the last."""

    spec = TEST_SEQUENCE

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        choices = visit.node.action_args["choices"]
        place = sum(1 for name in visit.marker_names() if name.startswith(_SEQUENCE_MARKER_PREFIX))
        choice = choices[min(place, len(choices) - 1)]
        await effects.record(Marker(f"{_SEQUENCE_MARKER_PREFIX}{place}", choice))
        return Pass(choice=choice)
