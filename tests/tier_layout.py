"""Where a test lives: the tier a folder gives, and the judgements that refuse a misplaced file.

Stdlib-only and importing nothing from ``tests.*``: ``scripts/mutation.py`` loads it by path, and a real ``tests``
import there would bind ``sys.modules["tests"]`` ahead of mutmut's own conftest load. Each judgement takes the tests
root and the mirrored package root, so it serves ``tests/`` against ``src/blizzard/`` and the exemplar's tree alike.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
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


def misplaced_root_module(path: Path, tests_root: Path, root_modules: Collection[str]) -> bool:
    """True when ``path`` is a ``test_*.py`` directly under ``tests_root`` that is not in ``root_modules``."""
    parts = _relative_parts(path, tests_root)
    return parts is not None and len(parts) == 1 and parts[0].startswith("test_") and parts[0] not in root_modules


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


def _relative_parts(path: Path, root: Path) -> tuple[str, ...] | None:
    try:
        return path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return None
