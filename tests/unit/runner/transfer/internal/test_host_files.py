"""The host-disk ``HostFiles`` on a real filesystem: resolution, existence, the copy dispatch and directory creation
keep :mod:`pathlib`'s and :mod:`shutil`'s semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.host_layout import HostLayout, hold_host
from blizzard.runner.runtime import StoreResetRefused, set_aside_sqlite, sqlite_store_file, sqlite_url
from blizzard.runner.transfer.host_files import NotAStoreFile
from blizzard.runner.transfer.internal.host_files import HostFiles


@pytest.fixture
def files() -> HostFiles[HostLayout]:
    return HostFiles(
        locate_store=sqlite_store_file,
        not_a_store_file=StoreResetRefused,
        url_of=sqlite_url,
        set_aside_store=set_aside_sqlite,
        layout_of=HostLayout,
        hold_host=hold_host,
    )


def test_resolution_follows_a_symlink(files: HostFiles[HostLayout], tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")

    assert files.resolved(tmp_path / "link") == (tmp_path / "real").resolve()


def test_resolution_makes_a_relative_path_absolute(
    files: HostFiles[HostLayout], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert files.resolved(Path("here")) == tmp_path.resolve() / "here"


def test_resolution_keeps_a_missing_tail(files: HostFiles[HostLayout], tmp_path: Path) -> None:
    assert files.resolved(tmp_path / "gone" / "deeper") == tmp_path.resolve() / "gone" / "deeper"


def test_existence_of_a_file_a_directory_a_missing_path_and_a_broken_symlink(
    files: HostFiles[HostLayout], tmp_path: Path
) -> None:
    (tmp_path / "file").write_text("x")
    (tmp_path / "broken").symlink_to(tmp_path / "nowhere")

    assert files.exists(tmp_path / "file")
    assert files.exists(tmp_path)
    assert not files.exists(tmp_path / "missing")
    assert not files.exists(tmp_path / "broken")


def test_a_directory_is_merged_into_an_existing_one(files: HostFiles[HostLayout], tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "nested").mkdir(parents=True)
    (src / "nested" / "marker").write_text("new")
    dst.mkdir()
    (dst / "kept").write_text("old")

    files.copy_path(src, dst)

    assert (dst / "nested" / "marker").read_text() == "new"
    assert (dst / "kept").read_text() == "old"


def test_a_file_is_copied_with_its_missing_parent_made(files: HostFiles[HostLayout], tmp_path: Path) -> None:
    src = tmp_path / "standing.md"
    src.write_text("prompt")
    dst = tmp_path / "a" / "b" / "standing.md"

    files.copy_path(src, dst)

    assert dst.read_text() == "prompt"


def test_an_absent_source_is_copied_as_nothing(files: HostFiles[HostLayout], tmp_path: Path) -> None:
    dst = tmp_path / "out" / "missing"

    files.copy_path(tmp_path / "missing", dst)

    assert not (tmp_path / "out").exists()


def test_directory_creation_makes_parents_and_tolerates_one_present(
    files: HostFiles[HostLayout], tmp_path: Path
) -> None:
    nested = tmp_path / "runners" / "r2"

    files.make_directory(nested)
    files.make_directory(nested)

    assert nested.is_dir()


def test_a_store_url_naming_no_sqlite_file_is_not_a_store_file(files: HostFiles[HostLayout]) -> None:
    with pytest.raises(NotAStoreFile):
        files.store_file("postgresql://db/blizzard")
