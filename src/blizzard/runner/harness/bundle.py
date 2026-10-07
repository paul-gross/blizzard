"""The operator's harness-config bundle: its models, errors, and the pure rules over them.

Each binding declares its own layout, so nothing here knows a harness by name. Reading,
validating, and publishing the bundle as an immutable snapshot is ``internal/bundle_publisher.py``."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
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


class CompanionEscapes(ValueError):
    """A companion reference resolves outside the harness directory it is relative to."""


def companion_path(reference: str) -> PurePosixPath | None:
    """The bundle-relative path a companion ``reference`` names, or ``None`` when it is skipped
    (an absolute or ``~`` path resolves outside the bundle by design); raises
    :class:`CompanionEscapes` when it climbs out of the harness directory."""
    if os.path.isabs(reference) or reference.startswith("~"):
        return None
    relative = PurePosixPath(os.path.normpath(reference))
    if relative.parts[:1] == ("..",) or str(relative) == ".":
        raise CompanionEscapes(reference)
    return relative


def require_published(config_dir: Path, snapshot: Path | None) -> Path:
    """``snapshot``, the published copy of the bundle at ``config_dir``; a configured bundle with
    no published snapshot is an error (:class:`HarnessBundleNotPublished`), never an empty one."""
    if snapshot is None:
        raise HarnessBundleNotPublished(config_dir)
    return snapshot
