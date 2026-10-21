"""The domain writer's ``bounce-envelope`` body validates against the wire model, with nothing dropped."""

from __future__ import annotations

import json

import pytest

from blizzard.hub.delivery.hub_node import BounceEnvelope as DomainEnvelope
from blizzard.hub.delivery.hub_node import FailedStep
from blizzard.hub.domain.chunk.delivery_read import DeliverySources, PrMarker
from blizzard.wire.bounce import BounceEnvelope

pytestmark = pytest.mark.unit

_PR = PrMarker("delivery-pr/acme/one/7", 1, "acme/one", 7, "https://forge.one/acme/one/pull/7")
_COMMITS = [
    {"repo": "acme/one", "branch": "feat/x", "commit": "a" * 40},
    {"repo": "acme/two", "branch": "feat/x", "commit": "b" * 40},
]
_STEP = FailedStep(name="tests", exit_code=2, output="boom", log_key="hub-log.tests")


def _written(*, step: FailedStep | None, pr_markers: list[PrMarker]) -> str:
    return DomainEnvelope.of(
        cause="failure",
        summary="delivery did not complete",
        commits=_COMMITS,
        sources=DeliverySources(pr_markers=pr_markers),
        step=step,
    ).serialize()


@pytest.mark.parametrize("step", [None, _STEP], ids=["no-step", "step"])
@pytest.mark.parametrize("pr_markers", [[], [_PR]], ids=["no-pr", "pr"])
def test_every_serialized_form_validates_and_round_trips_without_a_dropped_key(
    step: FailedStep | None, pr_markers: list[PrMarker]
) -> None:
    body = _written(step=step, pr_markers=pr_markers)

    parsed = BounceEnvelope.model_validate_json(body)

    assert parsed.model_dump(mode="json") == json.loads(body)
    assert [u.pr is not None for u in parsed.unlanded] == [bool(pr_markers), False]
    assert (parsed.step is not None) is (step is not None)
