"""The standing follow-latest migration policy: the precedence rule (`FollowLatest`) is a pure value object."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.graph.model import FollowLatest


@pytest.mark.parametrize(
    ("graph_policy", "hub_default", "expected"),
    [
        (None, False, False),  # inherit a hub that has not opted in — the shipped default
        (None, True, True),  # inherit a hub that has
        (True, False, True),  # graph overrides a false hub
        (False, True, False),  # graph overrides a true hub
        (True, True, True),
        (False, False, False),
    ],
)
def test_follow_latest_precedence(graph_policy: bool | None, hub_default: bool, expected: bool) -> None:
    assert FollowLatest.of(graph_policy, hub_default=hub_default).enabled is expected
