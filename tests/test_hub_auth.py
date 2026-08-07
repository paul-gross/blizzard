"""``FleetRequest.assert_owns`` (unit tier) — per-route runner_id confinement (issue #86a).

Bearer-token resolution is exercised at component tier (``tests/test_runner_enrollment.py``)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from blizzard.hub.api.auth import AuthMode, RunnerPrincipal
from blizzard.hub.api.fleet import FleetRequest
from blizzard.hub.config import RUNNER_AUTH_ENFORCE, RUNNER_AUTH_WARN

pytestmark = pytest.mark.unit

_PRINCIPAL = RunnerPrincipal(runner_id="runner-a", workspace_id="ws-a")


def _fleet(principal: RunnerPrincipal | None, mode: str) -> FleetRequest:
    # No config: `assert_owns` reads only the principal and the mode.
    return FleetRequest(principal, AuthMode(mode), config=None)  # type: ignore[arg-type]


def test_none_principal_is_never_a_mismatch_under_either_mode() -> None:
    # `require_runner_principal` already warn-logged (or 401'd) the missing/invalid
    # credential — a `None` principal reaching here is not itself flagged again.
    _fleet(None, RUNNER_AUTH_WARN).assert_owns("runner-a")
    _fleet(None, RUNNER_AUTH_ENFORCE).assert_owns("runner-a")


def test_matching_runner_id_is_never_a_mismatch_under_either_mode() -> None:
    _fleet(_PRINCIPAL, RUNNER_AUTH_WARN).assert_owns("runner-a")
    _fleet(_PRINCIPAL, RUNNER_AUTH_ENFORCE).assert_owns("runner-a")


def test_mismatch_under_warn_logs_and_does_not_raise() -> None:
    _fleet(_PRINCIPAL, RUNNER_AUTH_WARN).assert_owns("runner-b")  # no raise


def test_mismatch_under_enforce_raises_403() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _fleet(_PRINCIPAL, RUNNER_AUTH_ENFORCE).assert_owns("runner-b")
    assert excinfo.value.status_code == 403
    assert "runner-a" in excinfo.value.detail
    assert "runner-b" in excinfo.value.detail
