"""The GitHub-shaped forge delivery binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.hub.delivery.forge.IForgeDelivery` against the
GitHub-shaped forge — the ``blizzard-mock`` forge in tests (fronting the fixture
workspace's bare origins, so a merge is a *real* merge into bare ``main`` —
``verification.md``), GitHub in production. Confined to ``internal/`` (adapter
placement, ``bzh:dependency-inversion``); nothing outside the delivery feature
imports it.

**P6 contract stub.** Every method raises :class:`NotImplementedError`; the
walking-skeleton hub-track builder fills them against the mock forge's HTTP API.
The type conforms to the seam now, so the coordinator can be wired and tested with
a fake, and this binding swapped in without reshaping.
"""

from __future__ import annotations

from blizzard.hub.delivery.forge import (
    IForgeDelivery,
    LandingRequest,
    LandingResult,
    PrHandle,
    PrState,
)

_UNIMPLEMENTED = "GitHub forge delivery lands in the P6 walking skeleton"


class GitHubForgeDelivery:
    """The reference forge binding — a NotImplemented stub until P6 wires it."""

    def land(self, request: LandingRequest) -> LandingResult:
        raise NotImplementedError(_UNIMPLEMENTED)

    def open_pr(self, request: LandingRequest) -> PrHandle:
        raise NotImplementedError(_UNIMPLEMENTED)

    def check_pr(self, handle: PrHandle) -> PrState:
        raise NotImplementedError(_UNIMPLEMENTED)


def _conforms_forge_delivery(x: GitHubForgeDelivery) -> IForgeDelivery:
    return x
