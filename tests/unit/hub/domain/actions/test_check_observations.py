"""Durable empty observations are scoped to the repository, revision and visit epoch."""

from dataclasses import replace
from datetime import UTC, datetime

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.domain.actions.check_observations import marker_content, marker_name, observation, timeout_observation
from tests.support.land_action_doubles import _row  # pyright: ignore[reportPrivateUsage]


def test_observation_scoping_and_historical_timeout() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    marker = _row(
        ArtifactKind.ASSET, marker_name("re_1", "sha1"), marker_content("org/repo", "fast-forward", "sha1", now)
    )
    assert observation((marker,), 1, "re_1", "sha1") == now
    assert observation((marker,), 2, "re_1", "sha1") is None
    assert observation((marker,), 1, "re_1", "sha2") is None
    assert observation((marker,), 1, "re_2", "sha1") is None
    assert timeout_observation((marker,), "other", 1) is None
    assert timeout_observation((replace(marker, epoch=2),), marker.node_id, 1) is None
    result = timeout_observation((marker,), marker.node_id, 1)
    assert result is not None and result.cause == EscalationCause.MISSING_CHECKS
    assert "sha1" in result.detail and now.isoformat() in result.detail
    assert "not reconfirmed" in result.detail
