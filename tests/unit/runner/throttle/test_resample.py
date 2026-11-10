from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.throttle.floor import FloorHold, HoldKind
from blizzard.runner.throttle.resample import early_sample_due
from blizzard.runner.throttle.usage_limits import UsageLimitMark

_NOW = datetime(2026, 8, 1, tzinfo=UTC)
_BEFORE = _NOW - timedelta(seconds=1)
_AFTER = _NOW + timedelta(seconds=1)
_MARK = UsageLimitMark(_NOW, None, None, None, "harness", "", "lease", 1, 1, "worker")
_HOLD = FloorHold(HoldKind.MARK, "harness", None, _NOW, True)


@pytest.mark.parametrize(
    ("mark", "hold", "attempt", "now", "expected"),
    [
        (None, None, None, _NOW, False),
        (_MARK, None, None, _NOW, True),
        (_MARK, None, _BEFORE, _NOW, True),
        (_MARK, None, _NOW, _NOW, False),
        (_MARK, None, _AFTER, _NOW, False),
        (None, _HOLD, None, _BEFORE, False),
        (None, _HOLD, None, _NOW, True),
        (None, _HOLD, None, _AFTER, True),
        (None, _HOLD, _BEFORE, _NOW, True),
        (None, _HOLD, _NOW, _BEFORE, False),
        (None, _HOLD, _NOW, _NOW, False),
        (None, _HOLD, _AFTER, _AFTER, False),
        (_MARK, _HOLD, _BEFORE, _BEFORE, True),
        (_MARK, _HOLD, _NOW, _NOW, False),
    ],
)
def test_early_sample_boundaries(
    mark: UsageLimitMark | None, hold: FloorHold | None, attempt: datetime | None, now: datetime, expected: bool
) -> None:
    assert early_sample_due(mark, hold, last_attempt_at=attempt, now=now) is expected
