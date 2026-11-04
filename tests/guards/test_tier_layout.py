"""The folder layout: a folder gives its tests their tier, and a misplaced file is refused at collection."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from tests import suite_plugin
from tests.repo_files import repo_root
from tests.tier_layout import (
    declared_layout_marker,
    misplaced_root_module,
    non_package_folder,
    tier_of,
    unmirrored_folder,
)


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    tests = tmp_path / "tests"
    package = tmp_path / "src" / "pkg"
    for folder in (package, package / "hub", package / "hub" / "graphs"):
        folder.mkdir(parents=True, exist_ok=True)
    for folder in (package, package / "hub"):
        (folder / "__init__.py").write_text("")
    for folder in ("unit", "unit/hub", "unit/nowhere", "component", "guards", "guards/anything"):
        (tests / folder).mkdir(parents=True, exist_ok=True)
        (tests / folder / "__init__.py").write_text("")
    return tests, package


class _Item:
    """The slice of a collected item the layout step reads and writes."""

    def __init__(self, path: Path, markers: tuple[str, ...] = ()) -> None:
        self.path = path
        self.nodeid = path.name
        self.added: list[str] = []
        self._markers = markers

    def iter_markers(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=name) for name in self._markers]

    def add_marker(self, mark: pytest.MarkDecorator) -> None:
        self.added.append(mark.name)


def _layout(tests: Path, package: Path) -> tuple[tuple[Path, Path, frozenset[str]], ...]:
    return ((tests, package, frozenset[str]()),)


def _apply(*items: _Item) -> None:
    suite_plugin.apply_folder_layout(cast("list[pytest.Item]", list(items)))


def test_a_tier_folder_gives_its_tier(tmp_path: Path) -> None:
    tests, _ = _tree(tmp_path)
    assert tier_of(tests / "unit" / "hub" / "test_a.py", tests) == "unit"
    assert tier_of(tests / "guards" / "test_a.py", tests) == "unit"
    assert tier_of(tests / "component" / "test_a.py", tests) == "component"
    assert tier_of(tests / "test_a.py", tests) is None
    assert tier_of(tests / "crash" / "test_a.py", tests) is None
    assert tier_of(tmp_path / "elsewhere" / "unit" / "test_a.py", tests) is None


def test_a_declared_tier_marker_is_found() -> None:
    assert declared_layout_marker(["parametrize", "component"]) == "component"
    assert declared_layout_marker(["parametrize", "service"]) is None


def test_a_flat_module_outside_the_root_list_is_misplaced(tmp_path: Path) -> None:
    tests, _ = _tree(tmp_path)
    assert misplaced_root_module(tests / "test_new.py", tests, {"test_old.py"})
    assert not misplaced_root_module(tests / "test_old.py", tests, {"test_old.py"})
    assert not misplaced_root_module(tests / "unit" / "test_new.py", tests, set())
    assert not misplaced_root_module(tests / "conftest.py", tests, set())


def test_a_folder_without_an_init_is_not_a_package(tmp_path: Path) -> None:
    tests, _ = _tree(tmp_path)
    (tests / "unit" / "bare").mkdir()
    assert non_package_folder(tests / "unit" / "bare" / "test_a.py", tests) == tests / "unit" / "bare"
    assert non_package_folder(tests / "unit" / "hub" / "test_a.py", tests) is None


def test_a_folder_mirroring_no_package_is_refused_except_under_guards(tmp_path: Path) -> None:
    tests, package = _tree(tmp_path)
    assert unmirrored_folder(tests / "unit" / "hub" / "test_a.py", tests, package) is None
    assert unmirrored_folder(tests / "unit" / "test_a.py", tests, package) is None
    assert unmirrored_folder(tests / "unit" / "nowhere" / "test_a.py", tests, package) == tests / "unit" / "nowhere"
    assert unmirrored_folder(tests / "guards" / "anything" / "test_a.py", tests, package) is None


def test_the_layout_step_marks_a_test_with_its_folders_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests, package = _tree(tmp_path)
    monkeypatch.setattr(suite_plugin, "LAYOUT_TREES", _layout(tests, package))
    unit, component = _Item(tests / "unit" / "hub" / "test_a.py"), _Item(tests / "component" / "test_b.py")
    _apply(unit, component)
    assert (unit.added, component.added) == (["unit"], ["component"])


@pytest.mark.parametrize(
    ("relative", "markers", "reason"),
    [
        ("unit/hub/test_a.py", ("unit",), "declares the 'unit' marker"),
        ("component/test_a.py", ("component",), "declares the 'component' marker"),
        ("unit/nowhere/test_a.py", (), "mirrors no package"),
        ("test_flat.py", (), "a flat test module"),
    ],
)
def test_the_layout_step_refuses_a_misplaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, markers: tuple[str, ...], reason: str
) -> None:
    tests, package = _tree(tmp_path)
    monkeypatch.setattr(suite_plugin, "LAYOUT_TREES", _layout(tests, package))
    with pytest.raises(pytest.UsageError, match=reason):
        _apply(_Item(tests / relative, markers))


def test_the_layout_step_refuses_a_folder_that_is_not_a_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests, package = _tree(tmp_path)
    (tests / "guards" / "bare").mkdir()
    monkeypatch.setattr(suite_plugin, "LAYOUT_TREES", _layout(tests, package))
    with pytest.raises(pytest.UsageError, match=r"no __init__\.py"):
        _apply(_Item(tests / "guards" / "bare" / "test_a.py"))


def _collected(marker: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", marker, str(Path(__file__))],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def test_a_markerless_module_under_a_tier_folder_is_selected_by_that_tier_alone() -> None:
    assert "test_tier_layout.py::test_a_tier_folder_gives_its_tier" in _collected("unit")
    assert "test_tier_layout.py::test_a_tier_folder_gives_its_tier" not in _collected("component")
