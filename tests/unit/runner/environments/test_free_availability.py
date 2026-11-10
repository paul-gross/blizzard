from datetime import UTC, datetime

import pytest

from blizzard.runner.environments.free import has_free_environment
from blizzard.runner.environments.repository import EnvBinding


def _binding(environment_id: str) -> EnvBinding:
    return EnvBinding("chunk", environment_id, "/work", datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    ("pool", "held", "expected"),
    [
        (None, [], False),
        ((), [], True),
        ((), [_binding("a")], True),
        (("a",), [], True),
        (("a",), [_binding("a")], False),
        (("a", "b"), [_binding("a")], True),
        (("a", "b"), [_binding("a"), _binding("b")], False),
        (("a",), [_binding("other")], True),
    ],
)
def test_free_environment(pool: tuple[str, ...] | None, held: list[EnvBinding], expected: bool) -> None:
    assert has_free_environment(pool, held) is expected
