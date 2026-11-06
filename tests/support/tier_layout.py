"""Where a test lives: the tier a folder gives, and the judgements that refuse a misplaced file.

Stdlib-only and importing nothing from ``tests.*``: ``scripts/mutation.py`` loads it by path, and a real ``tests``
import there would bind ``sys.modules["tests"]`` ahead of mutmut's own conftest load. Each judgement takes the tests
root and the mirrored package root, so it serves ``tests/`` against ``src/blizzard/`` and the exemplar's tree alike.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

#: The folder directly under a tests root -> the tier it gives every test beneath it.
FOLDER_TIERS: dict[str, str] = {"unit": "unit", "guards": "unit", "component": "component"}

#: The tier markers a folder gives, which a test under that folder must not declare.
LAYOUT_MARKERS = frozenset(FOLDER_TIERS.values())

#: The folder whose sub-folders mirror no package.
GUARDS_FOLDER = "guards"


def tier_of(path: Path, tests_root: Path) -> str | None:
    """The tier the folder of ``path`` under ``tests_root`` gives, or None outside a tier folder."""
    parts = _relative_parts(path, tests_root)
    if parts is None or len(parts) < 2:
        return None
    return FOLDER_TIERS.get(parts[0])


def declared_layout_marker(marker_names: Iterable[str]) -> str | None:
    """The first ``unit``/``component`` marker among ``marker_names``, or None."""
    for name in marker_names:
        if name in LAYOUT_MARKERS:
            return name
    return None


def flat_test_module(path: Path, tests_root: Path) -> bool:
    """True when ``path`` is a ``test_*.py`` directly under ``tests_root``, where no test module may sit."""
    parts = _relative_parts(path, tests_root)
    return parts is not None and len(parts) == 1 and parts[0].startswith("test_")


def non_package_folder(path: Path, tests_root: Path) -> Path | None:
    """The first folder from the tier folder down to ``path`` that lacks an ``__init__.py``, or None."""
    parts = _relative_parts(path, tests_root)
    if parts is None or len(parts) < 2 or parts[0] not in FOLDER_TIERS:
        return None
    for depth in range(1, len(parts)):
        folder = tests_root.joinpath(*parts[:depth])
        if not (folder / "__init__.py").is_file():
            return folder
    return None


def unmirrored_folder(path: Path, tests_root: Path, package_root: Path) -> Path | None:
    """The first folder under a tier folder naming no package under ``package_root``, or None.

    The tier folder itself and everything under ``guards/`` mirror nothing.
    """
    parts = _relative_parts(path, tests_root)
    if parts is None or len(parts) < 3 or parts[0] not in FOLDER_TIERS or parts[0] == GUARDS_FOLDER:
        return None
    for depth in range(2, len(parts)):
        if not (package_root.joinpath(*parts[1:depth], "__init__.py")).is_file():
            return tests_root.joinpath(*parts[:depth])
    return None


#: The only modules a tier folder holds besides its test modules.
TIER_FOLDER_MODULES = frozenset({"__init__.py", "conftest.py"})


def tier_folder_helpers(tests_root: Path) -> list[Path]:
    """Every non-test module under a tier folder of ``tests_root``, sorted; shared helpers belong in ``support/``.

    Collection never sees an uncollected helper, so this reads the folders rather than judging a collected item.
    """
    return sorted(
        path
        for folder in FOLDER_TIERS
        for path in (tests_root / folder).rglob("*.py")
        if path.name not in TIER_FOLDER_MODULES and not path.name.startswith("test_")
    )


def _relative_parts(path: Path, root: Path) -> tuple[str, ...] | None:
    try:
        return path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return None
