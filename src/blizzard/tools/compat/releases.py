"""Releases from tag names, and the supported set a window selects — pure over strings.

A release is a ``vMAJOR.MINOR.PATCH`` or ``vMAJOR.MINOR.PATCH-rc.N`` tag; any other tag is
ignored. Candidates count as releases: the newest release of a line is its newest tag."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from blizzard.foundation.roles import domain_model

_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?")
_LINE_BRANCH = re.compile(r"release/(\d+)\.(\d+)")


@domain_model
@dataclass(frozen=True)
class Window:
    """The compatibility window a tree declares: its floor line and, while a major is opening, that major."""

    floor: str
    opening: str | None = None

    @property
    def floor_line(self) -> tuple[int, int]:
        major, minor = self.floor.split(".")
        return int(major), int(minor)


@domain_model
@dataclass(frozen=True)
class Release:
    """One release tag, parsed."""

    tag: str
    major: int
    minor: int
    patch: int
    candidate: int | None

    @property
    def line(self) -> str:
        return f"{self.major}.{self.minor}"

    def is_stable(self) -> bool:
        return self.candidate is None

    def order(self) -> tuple[int, int, int, int]:
        """Sorts oldest first: a stable release is newer than every candidate of its version."""
        return self.major, self.minor, self.patch, self.candidate if self.candidate is not None else 1 << 30


def parse_release(tag: str) -> Release | None:
    match = _TAG.fullmatch(tag)
    if match is None:
        return None
    major, minor, patch, candidate = match.groups()
    return Release(tag, int(major), int(minor), int(patch), None if candidate is None else int(candidate))


def parse_releases(tags: Iterable[str]) -> list[Release]:
    """Every release among ``tags``, oldest first."""
    return sorted((r for r in map(parse_release, tags) if r is not None), key=lambda r: r.order())


def supported(tags: Iterable[str], window: Window) -> list[Release]:
    """The releases a tree with ``window`` is held to, oldest first.

    The newest release on the floor line, and the newest of each major at or above the floor's, with
    duplicates collapsed. A floor no tag has reached gives an empty list."""
    floor = window.floor_line
    kept = [r for r in parse_releases(tags) if (r.major, r.minor) >= floor]
    chosen: dict[str, Release] = {}
    for release in kept:
        if (release.major, release.minor) == floor:
            chosen[f"line:{release.line}"] = release
        chosen[f"major:{release.major}"] = release
    return sorted(set(chosen.values()), key=lambda r: r.order())


def line_of(branch: str) -> str:
    """The ``MAJOR.MINOR`` a ``release/X.Y`` branch name carries."""
    match = _LINE_BRANCH.fullmatch(branch)
    if match is None:
        raise ValueError(f"{branch!r} is not a release branch, expected release/MAJOR.MINOR")
    return f"{match.group(1)}.{match.group(2)}"


def on_line(tags: Iterable[str], line: str) -> list[Release]:
    """Every release cut on ``line``, oldest first."""
    return [r for r in parse_releases(tags) if r.line == line]


def newest_stable(tags: Iterable[str]) -> Release | None:
    stable = [r for r in parse_releases(tags) if r.is_stable()]
    return stable[-1] if stable else None


def releases_document(window: Window, releases: list[Release]) -> dict[str, Any]:
    """The ``releases --json`` contract; other checks read it, and a field is only ever added."""
    return {
        "floor": window.floor,
        "opening": window.opening,
        "releases": [
            {"tag": r.tag, "line": r.line, "stable": r.is_stable(), "oldest": index == 0}
            for index, r in enumerate(releases)
        ],
    }
