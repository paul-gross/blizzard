from __future__ import annotations

import re

import pytest

from blizzard.foundation.runner_version import VersionRefusalReason
from blizzard.hub.cli.runtime import packaged_version_window
from blizzard.hub.domain.runners.version_window import (
    HANDSHAKE_SINCE,
    VersionWindow,
    VersionWindowError,
    admits,
    check_own_floor,
)

pytestmark = pytest.mark.unit

_SOURCE = "window.toml"


def test_the_packaged_window_is_the_committed_one() -> None:
    assert packaged_version_window() == VersionWindow(floor="0.2", opening=None)


def test_opening_is_read_when_present() -> None:
    window = VersionWindow.parse('floor = "0.10"\nopening = "1"\n', _SOURCE)
    assert window == VersionWindow(floor="0.10", opening="1")


@pytest.mark.parametrize(
    "text",
    [
        "floor = 0.2\n",
        'floor = "0"\n',
        'floor = "0.2.1"\n',
        'floor = "v0.2"\n',
        "",
        'floor = "0.2"\nopening = 1\n',
        'floor = "0.2"\nopening = "1.0"\n',
        'floor = "0.2"\nopenning = "1"\n',
        "floor = [",
    ],
)
def test_a_malformed_window_is_refused_by_name(text: str) -> None:
    with pytest.raises(VersionWindowError, match=re.escape(_SOURCE)):
        VersionWindow.parse(text, _SOURCE)


@pytest.mark.parametrize(
    ("text", "wording"),
    [
        ('floor = "0"\n', "`floor` must be a quoted string of MAJOR.MINOR"),
        ('floor = "0.2"\nopening = "x"\n', "`opening` must be a quoted string of a major number"),
        ('floor = "0.2"\nopening = 1\n', "got 1"),
        ("", "`floor` is required"),
    ],
)
def test_a_refusal_states_what_the_key_must_be(text: str, wording: str) -> None:
    with pytest.raises(VersionWindowError, match=re.escape(wording)):
        VersionWindow.parse(text, _SOURCE)


def test_the_packaged_window_is_well_formed() -> None:
    packaged = packaged_version_window()
    assert re.fullmatch(r"\d+\.\d+", packaged.floor)
    assert packaged.opening is None or packaged.opening.isdigit()


_WINDOW = VersionWindow(floor="0.2")


@pytest.mark.parametrize(
    ("runner", "reason"),
    [
        ("0.3.0", None),
        ("0.3.7", None),
        ("0.2.1", None),
        ("0.1.4", VersionRefusalReason.TOO_OLD),
        ("0.4.0", VersionRefusalReason.TOO_NEW),
        ("1.0.0", VersionRefusalReason.TOO_NEW),
        ("0.3.0.dev5+g1a2b3c", None),
        ("0.4.0.dev1+g1a2b3c", VersionRefusalReason.TOO_NEW),
        ("0.3.0rc2", None),
        ("0.1.9rc1", VersionRefusalReason.TOO_OLD),
        ("0.3.0+local", None),
        ("0.3", None),
    ],
    ids=lambda value: str(value),
)
def test_a_runner_is_judged_by_its_line_alone(runner: str, reason: VersionRefusalReason | None) -> None:
    assert admits("0.3.2", runner, _WINDOW).reason is reason


@pytest.mark.parametrize("runner", [None, "not a version", ""])
def test_an_absent_or_unparseable_version_is_admitted_below_handshake_since(runner: str | None) -> None:
    admission = admits("0.3.0", runner, _WINDOW)
    assert admission.admitted()
    assert admission.runner_version is None


@pytest.mark.parametrize("runner", [None, "not a version"])
def test_an_absent_version_is_refused_once_the_floor_reaches_handshake_since(runner: str | None) -> None:
    admission = admits("0.4.0", runner, VersionWindow(floor=HANDSHAKE_SINCE))
    assert admission.reason is VersionRefusalReason.UNVERSIONED


def test_a_too_old_refusal_names_both_versions_and_the_floor() -> None:
    detail = admits("0.3", "0.1.4", _WINDOW).detail()
    assert detail is not None
    assert "0.1.4" in detail and "0.3" in detail and "0.2" in detail


def test_a_too_new_refusal_tells_the_operator_to_upgrade_the_hub() -> None:
    assert admits("0.3.2", "0.4.0", _WINDOW).detail() == (
        "runner 0.4.0 is newer than this hub, 0.3.2; upgrade the hub first"
    )


def test_an_admission_carries_no_detail_and_the_hub_line_as_ceiling() -> None:
    admission = admits("0.3.2.dev4+gabc", "0.2.0", _WINDOW)
    assert admission.detail() is None
    assert admission.ceiling == "0.3"


def test_a_hub_below_its_own_floor_is_refused_naming_both() -> None:
    with pytest.raises(VersionWindowError, match=r"0\.1\.9.*0\.2"):
        check_own_floor("0.1.9", _WINDOW)


@pytest.mark.parametrize("hub", ["0.2.0", "0.2.0rc1", "0.3.0.dev2+gabc"])
def test_a_hub_at_or_above_its_floor_starts(hub: str) -> None:
    check_own_floor(hub, _WINDOW)
