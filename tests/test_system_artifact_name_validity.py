"""``is_valid_system_artifact_name`` — the sibling owner of system-artifact name validity
(``canon:one-owner``), a global slash-bearing namespace unlike a graph artifact's."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.artifacts import is_valid_system_artifact_name

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name",
    [
        "docket",
        "garden-axis",
        "garden.axis",
        "garden_axis",
        "garden/finding-format",
        "garden/proposal-format",
        "a/b/c",
    ],
)
def test_valid_names(name: str) -> None:
    assert is_valid_system_artifact_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/garden",
        "garden/",
        "garden//finding-format",
        "garden name",
        "-garden",
        "garden-",
        "garden--format",
    ],
)
def test_invalid_names(name: str) -> None:
    assert not is_valid_system_artifact_name(name)
