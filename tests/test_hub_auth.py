"""``assert_owns`` (unit tier) — the per-route runner_id confinement helper (issue #86a).

Pure function over :class:`~blizzard.hub.api.auth.RunnerPrincipal`, no FastAPI request
needed — the mode-dependent HTTP/log behavior is what's under test here;
``require_runner_principal``'s bearer-token resolution is exercised at component tier
against a real hub (``tests/test_runner_enrollment.py``), since it needs a real
registry to resolve against.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from blizzard.hub.api.auth import RunnerPrincipal, assert_owns
from blizzard.hub.config import RUNNER_AUTH_ENFORCE, RUNNER_AUTH_WARN

pytestmark = pytest.mark.unit

_PRINCIPAL = RunnerPrincipal(runner_id="runner-a", workspace_id="ws-a")


def test_none_principal_is_never_a_mismatch_under_either_mode() -> None:
    # `require_runner_principal` already warn-logged (or 401'd) the missing/invalid
    # credential — a `None` principal reaching here is not itself flagged again.
    assert_owns(None, "runner-a", mode=RUNNER_AUTH_WARN)
    assert_owns(None, "runner-a", mode=RUNNER_AUTH_ENFORCE)


def test_matching_runner_id_is_never_a_mismatch_under_either_mode() -> None:
    assert_owns(_PRINCIPAL, "runner-a", mode=RUNNER_AUTH_WARN)
    assert_owns(_PRINCIPAL, "runner-a", mode=RUNNER_AUTH_ENFORCE)


def test_mismatch_under_warn_logs_and_does_not_raise() -> None:
    assert_owns(_PRINCIPAL, "runner-b", mode=RUNNER_AUTH_WARN)  # no raise


def test_mismatch_under_enforce_raises_403() -> None:
    with pytest.raises(HTTPException) as excinfo:
        assert_owns(_PRINCIPAL, "runner-b", mode=RUNNER_AUTH_ENFORCE)
    assert excinfo.value.status_code == 403
    assert "runner-a" in excinfo.value.detail
    assert "runner-b" in excinfo.value.detail
