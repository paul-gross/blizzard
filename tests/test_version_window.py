from __future__ import annotations

import re
from pathlib import Path

import pytest

from blizzard.wire.version_window import VersionWindow, VersionWindowError

pytestmark = pytest.mark.unit


def _window(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "window.toml"
    path.write_text(text)
    return path


def test_the_packaged_window_is_the_committed_one() -> None:
    assert VersionWindow.load() == VersionWindow(floor="0.2", opening=None)


def test_opening_is_read_when_present(tmp_path: Path) -> None:
    window = VersionWindow.load(_window(tmp_path, 'floor = "0.10"\nopening = "1"\n'))
    assert window == VersionWindow(floor="0.10", opening="1")


def test_a_missing_file_is_refused_by_name(tmp_path: Path) -> None:
    missing = tmp_path / "absent.toml"
    with pytest.raises(VersionWindowError, match=re.escape("absent.toml")):
        VersionWindow.load(missing)


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
def test_a_malformed_window_is_refused_by_name(tmp_path: Path, text: str) -> None:
    path = _window(tmp_path, text)
    with pytest.raises(VersionWindowError, match=re.escape(path.name)):
        VersionWindow.load(path)


@pytest.mark.parametrize(
    ("text", "wording"),
    [
        ('floor = "0"\n', "`floor` must be a quoted string of MAJOR.MINOR"),
        ('floor = "0.2"\nopening = "x"\n', "`opening` must be a quoted string of a major number"),
        ('floor = "0.2"\nopening = 1\n', "got 1"),
        ("", "`floor` is required"),
    ],
)
def test_a_refusal_states_what_the_key_must_be(tmp_path: Path, text: str, wording: str) -> None:
    with pytest.raises(VersionWindowError, match=re.escape(wording)):
        VersionWindow.load(_window(tmp_path, text))


def test_loading_with_no_source_reads_the_packaged_file() -> None:
    packaged = VersionWindow.load()
    assert re.fullmatch(r"\d+\.\d+", packaged.floor)
    assert packaged.opening is None or packaged.opening.isdigit()
