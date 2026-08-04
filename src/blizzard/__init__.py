"""Blizzard — hub, runner, and CLI for orchestrating fleets of coding agents.

One repo, one wheel: this package ships both daemons, the CLI, and the
embedded frontend. See the per-package READMEs and blizzard-context for the rules
this code is held to.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

#: The version of the *installed* distribution — what this process is actually running.
#: Read from package metadata rather than written here as a literal, because
#: ``scripts/build-wheel.sh`` stamps the version into ``pyproject.toml`` and never touches
#: this module (pinned by
#: tests/test_pin_foundation.py::test_version_tracks_the_installed_distribution_metadata).
#: The fallback covers a source tree imported without an install, where there is no
#: metadata to read and no honest version to claim.
try:
    __version__ = _installed_version("blizzard")
except PackageNotFoundError:  # pragma: no cover — an uninstalled source tree
    __version__ = "0.0.0+unknown"
