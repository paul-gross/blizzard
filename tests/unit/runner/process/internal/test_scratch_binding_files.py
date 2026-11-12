from pathlib import Path

import pytest

from blizzard.runner.process.internal.scratch_binding_files import BINDING_FILENAME, ScratchBindingFiles


def test_no_binding_reads_as_none_and_creates_the_data_directory(tmp_path: Path) -> None:
    files = ScratchBindingFiles(tmp_path / "data")

    assert files.read_previous() is None
    assert (tmp_path / "data").is_dir()


def test_publish_then_read_round_trips_and_leaves_no_temp_file(tmp_path: Path) -> None:
    data = tmp_path / "data"
    files = ScratchBindingFiles(data)
    files.read_previous()

    files.publish(tmp_path / "scratch")

    assert files.read_previous() == tmp_path / "scratch"
    assert [entry.name for entry in data.iterdir()] == [BINDING_FILENAME]


def test_prepare_creates_an_owner_only_root(tmp_path: Path) -> None:
    root = tmp_path / "a" / "scratch"

    ScratchBindingFiles(tmp_path / "data").prepare(root)

    assert root.is_dir()
    assert not list(root.iterdir())


def test_probe_rejects_a_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ScratchBindingFiles(tmp_path / "data").probe(tmp_path / "absent")
