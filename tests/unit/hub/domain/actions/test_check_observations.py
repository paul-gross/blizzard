"""Durable empty observations are scoped to the repository, revision and visit epoch."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.domain.actions.check_observations import (
    Eligibility,
    eligibility,
    marker_content,
    marker_name,
    missing_checks,
    observation,
    timeout_observation,
)
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


def test_observation_uses_first_marker_in_current_epoch() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    name = marker_name("re_1", "sha1")
    old = _row(ArtifactKind.ASSET, name, marker_content("org/repo", "fast-forward", "sha1", now))
    first = replace(old, epoch=2, data=marker_content("org/repo", "fast-forward", "sha1", now + timedelta(minutes=1)))
    later = replace(first, data=marker_content("org/repo", "fast-forward", "sha1", now + timedelta(minutes=5)))
    assert observation((old, first, later), 2, "re_1", "sha1") == now + timedelta(minutes=1)


@pytest.mark.parametrize("timed_out", [False, True])
def test_missing_checks_remedy_names_captured_terms(timed_out: bool) -> None:
    detail = missing_checks(
        "org/repo", "fast-forward", "sha", datetime(2026, 1, 2, tzinfo=UTC), timed_out=timed_out
    ).detail
    assert "strict (allow_no_ci=false)" in detail
    assert "Configure CI checks" in detail
    assert "explicitly enable allow_no_ci" in detail
    assert "requeue to capture the new terms" in detail


def test_eligibility_decisions_are_scoped_and_require_a_current_empty_read() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    marker = _row(
        ArtifactKind.ASSET, marker_name("re_1", "sha1"), marker_content("org/repo", "fast-forward", "sha1", now)
    )

    def decide(
        *,
        green: bool = False,
        empty: bool = True,
        allow: bool = False,
        epoch: int = 1,
        sha: str = "sha1",
        at: datetime = now,
    ) -> Eligibility:
        return eligibility(
            green=green,
            empty=empty,
            allow_no_ci=allow,
            markers=(marker,),
            epoch=epoch,
            repository="re_1",
            sha=sha,
            now=at,
        ).outcome

    assert decide(green=True, empty=False) is Eligibility.ELIGIBLE
    assert decide(empty=False, allow=True, at=now + timedelta(hours=1)) is Eligibility.INELIGIBLE
    assert decide(allow=True) is Eligibility.ELIGIBLE
    assert decide(epoch=2) is Eligibility.RECORD
    assert decide(sha="sha2") is Eligibility.RECORD
    assert decide(at=now + timedelta(minutes=9, seconds=59)) is Eligibility.WAIT
    assert decide(at=now + timedelta(minutes=10)) is Eligibility.ESCALATE
