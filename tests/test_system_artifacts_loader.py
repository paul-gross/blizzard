"""``hub/system_artifacts/__init__.py`` — the packaged ``ArtifactScope.SYSTEM`` set's own
loader (unit tier), mirroring ``tests/test_graph_artifacts_loader.py``'s treatment of
``hub/graphs``: one file per name, resolved fresh on every read, a malformed derived name
failing loudly rather than being silently skipped."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.system_artifacts import PackagedSystemArtifacts, SystemArtifactNameInvalid

pytestmark = pytest.mark.unit


def test_empty_root_publishes_nothing(tmp_path: Path) -> None:
    packaged = PackagedSystemArtifacts(tmp_path)
    assert packaged.files == []
    assert packaged.named("anything") is None


def test_a_top_level_file_is_named_by_its_stem(tmp_path: Path) -> None:
    (tmp_path / "docket.md").write_text("the docket text")
    packaged = PackagedSystemArtifacts(tmp_path)

    hit = packaged.named("docket")
    assert hit is not None
    assert hit.text == "the docket text"


def test_a_nested_file_is_named_by_its_slash_bearing_relative_path(tmp_path: Path) -> None:
    nested = tmp_path / "garden"
    nested.mkdir()
    (nested / "finding-format.md").write_text("the format text")
    packaged = PackagedSystemArtifacts(tmp_path)

    hit = packaged.named("garden/finding-format")
    assert hit is not None
    assert hit.text == "the format text"


def test_files_is_sorted_by_path(tmp_path: Path) -> None:
    garden = tmp_path / "garden"
    garden.mkdir()
    (tmp_path / "docket.md").write_text("d")
    (garden / "proposal-format.md").write_text("p")
    (garden / "finding-format.md").write_text("f")
    packaged = PackagedSystemArtifacts(tmp_path)

    assert [f.name for f in packaged.files] == ["docket", "garden/finding-format", "garden/proposal-format"]


def test_a_non_markdown_file_is_never_published(tmp_path: Path) -> None:
    (tmp_path / "README.txt").write_text("not a system artifact")
    packaged = PackagedSystemArtifacts(tmp_path)

    assert packaged.files == []


def test_a_readme_is_never_published_even_though_it_carries_the_suffix(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("directory documentation, not a document")
    garden = tmp_path / "garden"
    garden.mkdir()
    (garden / "README.md").write_text("same, nested")
    (garden / "finding-format.md").write_text("f")
    packaged = PackagedSystemArtifacts(tmp_path)

    assert [f.name for f in packaged.files] == ["garden/finding-format"]


def test_a_malformed_derived_name_raises_naming_it_and_its_path(tmp_path: Path) -> None:
    garden = tmp_path / "garden "
    garden.mkdir()
    (garden / "finding-format.md").write_text("f")
    packaged = PackagedSystemArtifacts(tmp_path)

    with pytest.raises(SystemArtifactNameInvalid) as exc_info:
        _ = packaged.files
    assert "garden /finding-format" in str(exc_info.value)
