"""Plan the runner-owned path rewrites and copies for an ingest."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path


def rebase_path_keys(
    paths: Mapping[tuple[str, ...], Path],
    keys: Sequence[tuple[str, ...]],
    source_root: Path,
    runner_prefix: Path,
    destination: Path,
) -> tuple[dict[tuple[str, ...], Path], tuple[tuple[Path, Path], ...]]:
    """Return replacements and ordered copy pairs without observing the filesystem."""
    replacements: dict[tuple[str, ...], Path] = {}
    files: list[tuple[Path, Path]] = []
    for key in keys:
        if key not in paths:
            continue
        path = paths[key]
        located = path if path.is_absolute() else source_root / path
        if not located.is_relative_to(source_root):
            continue
        inside = located.relative_to(source_root)
        if inside.parts[: len(runner_prefix.parts)] == runner_prefix.parts:
            inside = Path(*inside.parts[len(runner_prefix.parts) :])
        copied = destination / inside
        files.append((located, copied))
        replacements[key] = copied
    return replacements, tuple(files)
