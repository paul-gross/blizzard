"""``scripts/image-tags.sh`` — the pure function deciding the GHCR tag fan-out for
a release tag (issue #189). Pulled out of workflow YAML precisely so this is
unit-testable: the logic that decides whether ``latest`` moves lives here, not
buried in ``.github/workflows/release.yml``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "image-tags.sh"


def _run(tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(_SCRIPT), tag], capture_output=True, text=True)


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v1.2.3", ["1.2.3", "1.2", "latest"]),
        ("v0.1.0", ["0.1.0", "0.1", "latest"]),
        ("v10.20.30", ["10.20.30", "10.20", "latest"]),
    ],
)
def test_stable_tag_fans_out_to_exact_minor_and_latest(tag: str, expected: list[str]) -> None:
    result = _run(tag)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == expected


@pytest.mark.parametrize("tag", ["v1.2.3-rc.1", "v1.2.3-rc.10", "v0.1.0-rc.1"])
def test_prerelease_tag_fans_out_to_its_exact_version_only(tag: str) -> None:
    result = _run(tag)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines == [tag[1:]]
    assert "latest" not in lines
    # never a minor line either — a prerelease must not move the X.Y tag.
    assert all(line.count(".") != 1 for line in lines)


@pytest.mark.parametrize(
    "bad_tag",
    [
        "1.2.3",  # no v prefix
        "release-1.2.3",  # no v prefix
        "v1.2",  # missing patch
        "v1",  # missing minor + patch
        "vabc",  # not numeric
        "v1.2.3.4",  # too many components
    ],
)
def test_a_malformed_ref_is_rejected(bad_tag: str) -> None:
    result = _run(bad_tag)
    assert result.returncode != 0
    assert result.stdout == ""
