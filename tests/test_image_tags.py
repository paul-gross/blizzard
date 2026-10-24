"""``scripts/image-tags.sh`` — the pure function deciding the GHCR tag fan-out for
a release tag. Pulled out of workflow YAML precisely so this is
unit-testable: the logic that decides whether ``latest`` moves lives here, not
buried in ``.github/workflows/release.yml``.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_SCRIPT = repo_root() / "scripts" / "image-tags.sh"


def _run(tag: str, *repo_tags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(_SCRIPT), tag, *repo_tags], capture_output=True, text=True)


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


_REPO_TAGS = ["v0.1.0", "v0.2.0-rc.1", "v0.2.0", "v0.2.1", "v0.10.0", "v0.10.0-rc.2", "not-a-tag"]


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.2.0", ["0.2.0", "0.2"]),
        ("v0.2.1", ["0.2.1", "0.2"]),
        ("v0.10.0", ["0.10.0", "0.10", "latest"]),
    ],
)
def test_latest_goes_only_to_the_highest_stable_version_compared_numerically(tag: str, expected: list[str]) -> None:
    result = _run(tag, *_REPO_TAGS)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == expected


def test_a_candidate_higher_than_the_tag_does_not_hold_latest_back() -> None:
    assert _run("v0.2.0", "v0.3.0-rc.1").stdout.splitlines() == ["0.2.0", "0.2", "latest"]


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
