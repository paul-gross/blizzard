"""The floor's grace deadline projected through the throttle status wire view."""

from datetime import UTC, datetime

import pytest

from blizzard.runner.api.control import throttle_view
from blizzard.runner.status.view import ThrottleStatus
from blizzard.runner.throttle.floor import floor_answer
from blizzard.runner.throttle.gate import GateVerdict
from tests.support.runner_fakes import usage_limit_mark


@pytest.mark.parametrize("sampled, expected", [(True, "2026-10-01T14:00:00Z"), (False, None)])
def test_hold_grace_deadline_projects_to_wire(sampled: bool, expected: str | None) -> None:
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    floor = floor_answer(slug="plan", sampled=sampled, mark=usage_limit_mark(now), sample=None, clear=None, now=now)
    view = throttle_view(ThrottleStatus.of(GateVerdict(floor=floor), braked=False))

    assert view.floor.hold is not None
    assert view.floor.hold.model_dump(mode="json")["grace_opens_at"] == expected
