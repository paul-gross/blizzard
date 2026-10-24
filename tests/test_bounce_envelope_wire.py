"""The domain writer's ``bounce-envelope`` body validates against the wire model, with nothing dropped."""

from __future__ import annotations

import json

import pytest

from blizzard.hub.delivery.hub_node import BouncedAction
from blizzard.hub.delivery.hub_node import BounceEnvelope as DomainEnvelope
from blizzard.hub.domain.chunk.delivery_read import DeliverySources, PrMarker
from blizzard.wire.bounce import BounceEnvelope

pytestmark = pytest.mark.unit

_PR = PrMarker("delivery-pr/acme/one/7", 1, "acme/one", 7, "https://forge.one/acme/one/pull/7")
_COMMITS = [
    {"repo": "acme/one", "branch": "feat/x", "commit": "a" * 40},
    {"repo": "acme/two", "branch": "feat/x", "commit": "b" * 40},
]
_ACTION = BouncedAction(name="land", choice="failure", log_artifact="hub-log.land")
# A body stored before hub steps stopped running commands: a failed step, and no action.
_LEGACY_STEP_BODY = {
    "cause": "failure",
    "detail": "acme/one did not land: `tests` exited 2",
    "unlanded": [{"repo": "acme/one", "branch": "feat/x", "commit": "a" * 40, "pr": None}],
    "step": {"name": "tests", "exit_code": 2, "output_tail": "boom", "log_artifact": "hub-log.tests"},
}


def _written(*, action: BouncedAction | None, pr_markers: list[PrMarker]) -> str:
    return DomainEnvelope.of(
        cause="failure",
        summary="delivery did not complete",
        commits=_COMMITS,
        sources=DeliverySources(pr_markers=pr_markers),
        action=action,
    ).serialize()


@pytest.mark.parametrize(
    "action",
    [None, _ACTION, BouncedAction(name="land", choice="failure", log_artifact=None)],
    ids=["no-action", "action", "action-no-log"],
)
@pytest.mark.parametrize("pr_markers", [[], [_PR]], ids=["no-pr", "pr"])
def test_every_serialized_form_validates_and_round_trips_without_a_dropped_key(
    action: BouncedAction | None, pr_markers: list[PrMarker]
) -> None:
    body = _written(action=action, pr_markers=pr_markers)

    parsed = BounceEnvelope.model_validate_json(body)

    assert parsed.model_dump(mode="json") == json.loads(body)
    assert [u.pr is not None for u in parsed.unlanded] == [bool(pr_markers), False]
    assert json.loads(body)["step"] is None
    assert (parsed.action is not None) is (action is not None)


def test_a_pass_bounce_reads_the_action_and_the_choice_it_answered() -> None:
    body = json.loads(_written(action=_ACTION, pr_markers=[]))

    assert body["detail"] == "acme/one, acme/two did not land: `land` answered failure"
    assert body["action"] == {"name": "land", "choice": "failure", "log_artifact": "hub-log.land"}


def test_a_stored_step_envelope_still_validates_with_no_action() -> None:
    parsed = BounceEnvelope.model_validate(_LEGACY_STEP_BODY)

    assert parsed.action is None
    assert parsed.model_dump(mode="json", exclude={"action"}) == _LEGACY_STEP_BODY
