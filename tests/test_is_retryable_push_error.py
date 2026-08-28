"""``scripts/is-retryable-push-error.sh`` — the pure predicate deciding whether a
GHCR push's captured output names a transient error worth retrying: a 403
secondary rate limit, a 429, or a 5xx. Pulled out of the
dev-image job's retry loop precisely so the classification is unit-testable
here rather than buried in workflow YAML.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "is-retryable-push-error.sh"


def _run(log: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(_SCRIPT)], input=log, capture_output=True, text=True)


@pytest.mark.parametrize(
    "log",
    [
        "denied: permission_denied: Error from intermediary with HTTP status code 403 "
        '"Forbidden" "message": "You have exceeded a secondary rate limit. Please wait '
        'a few minutes before you try again."',
        "received unexpected HTTP status: 429 Too Many Requests",
        "failed to push: http status code 429",
        "failed to push: http status code 500",
        "failed to push: http status code 503",
    ],
)
def test_a_transient_registry_error_is_retryable(log: str) -> None:
    result = _run(log)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "log",
    [
        "denied: permission_denied: Error from intermediary with HTTP status code 403 "
        '"Forbidden" "message": "installation not allowed to Write organization package"',
        "unauthorized: authentication required",
        "manifest invalid: manifest referenced by tag does not match the manifest list",
        "",
    ],
)
def test_a_non_retryable_error_is_not_retryable(log: str) -> None:
    result = _run(log)
    assert result.returncode == 1
