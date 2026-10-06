"""``FleetRequest.assert_owns`` (unit tier) — confining a route that addresses a runner to the caller.

Bearer-token resolution is exercised at component tier (``tests/test_runner_enrollment.py``)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from blizzard.hub.api.auth import RunnerPrincipal
from blizzard.hub.api.fleet import FleetRequest

pytestmark = pytest.mark.unit

_PRINCIPAL = RunnerPrincipal(runner_id="runner-a", runner_name="r-claude", workspace_id="ws-a")


def _fleet(principal: RunnerPrincipal) -> FleetRequest:
    # No config: `assert_owns` reads only the principal.
    return FleetRequest(principal, config=None)  # type: ignore[arg-type]


def test_the_callers_own_runner_id_is_never_a_mismatch() -> None:
    _fleet(_PRINCIPAL).assert_owns("runner-a")


def test_another_runners_id_is_refused_403() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _fleet(_PRINCIPAL).assert_owns("runner-b")
    assert excinfo.value.status_code == 403
    assert "runner-a" in excinfo.value.detail
    assert "runner-b" in excinfo.value.detail
