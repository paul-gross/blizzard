"""Which route releases are an occurrence of their own (``RouteReleaseCause``)."""

from __future__ import annotations

from blizzard.hub.domain.chunk.model import OWN_OCCURRENCE_RELEASE_CAUSES, RouteReleaseCause

_OWN_OCCURRENCE = {
    RouteReleaseCause.DETACH: True,
    RouteReleaseCause.RETIREMENT: True,
    RouteReleaseCause.RELEASE: True,
    RouteReleaseCause.STOP: False,
    RouteReleaseCause.COMPLETE: False,
    RouteReleaseCause.REQUEUE: False,
    RouteReleaseCause.MIGRATION: False,
    RouteReleaseCause.HUB_STEP: False,
    RouteReleaseCause.DELIVERY: False,
}


def test_only_a_detach_a_retirement_and_the_holders_release_are_their_own_occurrence() -> None:
    assert {cause: cause in OWN_OCCURRENCE_RELEASE_CAUSES for cause in RouteReleaseCause} == _OWN_OCCURRENCE
