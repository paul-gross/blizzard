"""Blizzard — hub, runner, and CLI for orchestrating fleets of coding agents.

One repo, one wheel: this package ships both daemons, the CLI, and the
embedded frontend. See the per-package READMEs and blizzard-context for the rules
this code is held to.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

#: The version of the *installed* distribution — what this process is actually running.
#:
#: Read from package metadata rather than written here as a literal, because the literal
#: cannot stay true. ``scripts/build-wheel.sh`` stamps the wheel's version by rewriting
#: ``pyproject.toml``'s ``[project] version`` (dev builds take ``0.<milestone>.0.dev<run>``,
#: a tag release takes the tag) and never touches this module — so a hardcoded value
#: reported ``0.1.0`` out of every artifact ever built, whatever it really was. That is
#: the single string ``blizzard --version``, both daemons' ``GET /api/health``, and both
#: OpenAPI documents answer with, and it is how an operator tells one deployed build from
#: another: ``blizzard-context:/verification/blizzard.md``'s rollback drill passes only
#: when ``/api/health`` reports *the older* version after the swap.
#:
#: The fallback covers a source tree being imported without an install at all, where
#: there is no metadata to read and no honest version to claim.
try:
    __version__ = _installed_version("blizzard")
except PackageNotFoundError:  # pragma: no cover — an uninstalled source tree
    __version__ = "0.0.0+unknown"
