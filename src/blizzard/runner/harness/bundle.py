"""The operator's harness-config bundle: read, validate, and publish as an immutable snapshot.

Each binding declares its own layout, so nothing here knows a harness by name. The bundle is
only read; its supported material is staged, named by a hash of its content, and ``current``
is swapped to it atomically — a failure leaves the previous pointer and snapshots untouched."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from blizzard.foundation.roles import domain_model
from blizzard.runner.config_table import ConfigError

# The runner-owned directory the published harness-config snapshots live under.
HARNESS_CONFIG_DIRNAME = "harness-config"
SNAPSHOTS_DIRNAME = "snapshots"
STAGING_DIRNAME = "staging"
CURRENT_LINK = "current"
# A staging directory older than this belongs to a publish that died; a younger one may be live.
_STALE_STAGING_SECONDS = 3600.0


class HarnessBundleError(ConfigError):
    """The bundle cannot be loaded; names the offending path and the cause."""

    def __init__(self, path: Path, cause: str) -> None:
        super().__init__(f"harness config bundle: {path}: {cause}")
        self.path = path
        self.cause = cause


class HarnessBundleNotPublished(ConfigError):
    """A harness-config bundle is configured but no snapshot of it is published yet."""

    def __init__(self, config_dir: Path) -> None:
        super().__init__("config_dir is configured but no snapshot is published; restart the runner")
        self.config_dir = config_dir


@domain_model
@dataclass(frozen=True)
class EntryPoint:
    """One top-level name a harness directory may hold: a JSON-object file or a directory.

    ``companions`` maps a parsed JSON entry point to the file references inside it that
    resolve relative to the file; ``None`` declares that the harness resolves none."""

    name: str
    is_dir: bool = False
    companions: Callable[[Mapping[str, Any]], tuple[str, ...]] | None = None


@domain_model
@dataclass(frozen=True)
class HarnessLayout:
    """One harness's slice of the bundle: its directory name and recognized entry points."""

    dirname: str
    entry_points: tuple[EntryPoint, ...]
    #: Rewrites the staged copy in place before the snapshot is named; raising aborts the publish.
    compose: Callable[[HarnessComposition], None] | None = None


@domain_model
@dataclass(frozen=True)
class HarnessComposition:
    """What a layout's compose hook is given: the staged directory it may rewrite and the
    operator's source directory, so a failure can name the native path."""

    staged_dir: Path
    source_dir: Path


@domain_model
@dataclass(frozen=True)
class HarnessSource:
    """One harness directory found in the bundle and where its snapshot copy lives."""

    dirname: str
    source_dir: Path
    effective_dir: Path
    entry_points: tuple[str, ...]


@domain_model
@dataclass(frozen=True)
class BundleSnapshot:
    """The facts of a loaded bundle — names and paths only, never file contents."""

    source_dir: Path
    path: Path
    harnesses: tuple[HarnessSource, ...]

    def summary(self) -> str:
        found = "; ".join(f"{h.dirname}: {', '.join(h.entry_points) or 'no entry points'}" for h in self.harnesses)
        return f"harness config bundle {self.source_dir} -> snapshot {self.path} ({found or 'no harness directories'})"


@domain_model
@dataclass(frozen=True)
class _Plan:
    """A validated bundle: each harness's source dir, entry points present, and files to copy."""

    source_dir: Path
    harnesses: tuple[tuple[HarnessLayout, Path, tuple[str, ...], tuple[PurePosixPath, ...]], ...]


def inspect_bundle(config_dir: Path, layouts: tuple[HarnessLayout, ...]) -> tuple[HarnessSource, ...]:
    """Validate the bundle and report its harness directories, with ``effective_dir`` unset
    (equal to the source); raises :class:`HarnessBundleError` on any violation."""
    plan = _plan(config_dir, layouts)
    return tuple(HarnessSource(layout.dirname, src, src, present) for layout, src, present, _ in plan.harnesses)


def publish_bundle(config_dir: Path, runtime_root: Path, layouts: tuple[HarnessLayout, ...]) -> BundleSnapshot:
    """Validate the bundle, stage a copy of its supported material, name it by content, and
    point ``current`` at it. The bundle itself is only ever read."""
    plan = _plan(config_dir, layouts)
    effective = runtime_root / HARNESS_CONFIG_DIRNAME
    snapshots = effective / SNAPSHOTS_DIRNAME
    staging_root = effective / STAGING_DIRNAME
    try:
        snapshots.mkdir(parents=True, exist_ok=True)
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = staging_root / uuid.uuid4().hex
        staging.mkdir()
        _clear_stale_staging(staging_root, now=staging.stat().st_mtime)
    except OSError as exc:
        raise HarnessBundleError(effective, f"cannot prepare the effective directory: {exc}") from exc
    try:
        _stage(plan, staging)
        _compose(plan, staging)
        snapshot = snapshots / _tree_hash(staging)
        try:
            if snapshot.exists():
                shutil.rmtree(staging)
            else:
                try:
                    os.rename(staging, snapshot)
                except OSError:
                    # A concurrent publish of the identical bundle won the name.
                    if not snapshot.exists():
                        raise
                    shutil.rmtree(staging, ignore_errors=True)
            _point_current_at(effective, snapshot)
        except OSError as exc:
            raise HarnessBundleError(effective, f"cannot publish the snapshot: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return BundleSnapshot(
        source_dir=plan.source_dir,
        path=snapshot,
        harnesses=tuple(
            HarnessSource(layout.dirname, src, snapshot / layout.dirname, present)
            for layout, src, present, _ in plan.harnesses
        ),
    )


def published_snapshot(runtime_root: Path) -> Path | None:
    """The snapshot ``current`` resolves to, or ``None`` when nothing is published."""
    link = runtime_root / HARNESS_CONFIG_DIRNAME / CURRENT_LINK
    return link.resolve() if link.is_dir() else None


def require_published(config_dir: Path, snapshot: Path | None) -> Path:
    """``snapshot``, the published copy of the bundle at ``config_dir``; a configured bundle with
    no published snapshot is an error (:class:`HarnessBundleNotPublished`), never an empty one."""
    if snapshot is None:
        raise HarnessBundleNotPublished(config_dir)
    return snapshot


def _plan(config_dir: Path, layouts: tuple[HarnessLayout, ...]) -> _Plan:
    if not config_dir.is_dir():
        raise HarnessBundleError(config_dir, "the configured bundle directory is missing or is not a directory")
    by_name = {layout.dirname: layout for layout in layouts}
    harnesses = []
    for entry in sorted(config_dir.iterdir()):
        layout = by_name.get(entry.name)
        if layout is None or not entry.is_dir():
            known = ", ".join(sorted(by_name))
            raise HarnessBundleError(entry, f"not a recognized harness directory (expected one of: {known})")
        harnesses.append((layout, entry, *_plan_harness(layout, entry)))
    return _Plan(config_dir, tuple(harnesses))


def _plan_harness(layout: HarnessLayout, directory: Path) -> tuple[tuple[str, ...], tuple[PurePosixPath, ...]]:
    declared = {entry.name: entry for entry in layout.entry_points}
    present: list[str] = []
    companions: list[PurePosixPath] = []
    for child in sorted(directory.iterdir()):
        entry = declared.get(child.name)
        if entry is None:
            continue
        if entry.is_dir != child.is_dir():
            raise HarnessBundleError(child, f"must be a {'directory' if entry.is_dir else 'file'}")
        present.append(child.name)
        if not entry.is_dir:
            companions.extend(_companions(child, directory, entry.companions))
    reached = set(present) | {ref.parts[0] for ref in companions}
    for child in sorted(directory.iterdir()):
        if child.name not in reached:
            allowed = ", ".join(sorted(declared))
            raise HarnessBundleError(child, f"not a recognized entry point (expected: {allowed})")
    return tuple(present), tuple(dict.fromkeys(companions))


def _companions(
    file: Path, directory: Path, extract: Callable[[Mapping[str, Any]], tuple[str, ...]] | None
) -> Iterator[PurePosixPath]:
    try:
        document = json.loads(file.read_text())
    except (OSError, ValueError) as exc:
        raise HarnessBundleError(file, f"cannot read as JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise HarnessBundleError(file, "must hold a JSON object")
    for reference in extract(document) if extract else ():
        if os.path.isabs(reference) or reference.startswith("~"):
            continue
        relative = PurePosixPath(os.path.normpath(reference))
        if relative.parts[:1] == ("..",) or str(relative) == ".":
            raise HarnessBundleError(file, f"reference {reference!r} escapes {directory}")
        if not (directory / relative).exists():
            raise HarnessBundleError(directory / relative, f"referenced by {file.name} but does not exist")
        yield relative


def _stage(plan: _Plan, staging: Path) -> None:
    for layout, source, present, companions in plan.harnesses:
        target = staging / layout.dirname
        target.mkdir()
        for relative in (PurePosixPath(name) for name in present), companions:
            for item in relative:
                _copy(source / item, target / item)


def _compose(plan: _Plan, staging: Path) -> None:
    for layout, source, _, _ in plan.harnesses:
        if layout.compose is not None:
            layout.compose(HarnessComposition(staging / layout.dirname, source))


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        if source.is_dir():
            shutil.copytree(source, destination, symlinks=False, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
    except (OSError, shutil.Error) as exc:
        raise HarnessBundleError(source, f"cannot copy into the snapshot: {exc}") from exc


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        if path.is_file():
            digest.update(b"%o\0" % stat.S_IMODE(path.stat().st_mode))
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _point_current_at(effective: Path, snapshot: Path) -> None:
    pending = effective / f".{CURRENT_LINK}.{uuid.uuid4().hex}"
    pending.symlink_to(snapshot.relative_to(effective))
    try:
        os.replace(pending, effective / CURRENT_LINK)
    except OSError:
        pending.unlink(missing_ok=True)
        raise


def _clear_stale_staging(staging_root: Path, *, now: float) -> None:
    """Remove staging directories a dead publish left behind; ``now`` is the filesystem's own
    clock, read off the directory just created."""
    cutoff = now - _STALE_STAGING_SECONDS
    for stale in staging_root.iterdir():
        if stale.stat().st_mtime < cutoff:
            shutil.rmtree(stale, ignore_errors=True)
