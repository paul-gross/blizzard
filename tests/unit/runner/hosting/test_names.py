"""The one rule naming a hosted runner: its non-blank stored name, else its identity row's, else its id."""

from __future__ import annotations

import pytest

from blizzard.runner.hosting.names import hosted_name


@pytest.mark.parametrize(
    ("stored", "identity_name", "expected"),
    [
        ("alpha", "beta", "alpha"),
        ("  alpha  ", "beta", "alpha"),
        (None, "beta", "beta"),
        ("", "beta", "beta"),
        ("   ", "beta", "beta"),
        ("alpha", None, "alpha"),
        (None, None, "rn_a"),
        (None, "", "rn_a"),
        (None, "   ", "rn_a"),
        ("   ", "   ", "rn_a"),
    ],
)
def test_a_runner_goes_by_its_stored_name_else_its_identity_name_else_its_id(
    stored: str | None, identity_name: str | None, expected: str
) -> None:
    assert hosted_name(stored, identity_name, "rn_a") == expected
