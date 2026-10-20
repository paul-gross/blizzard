"""The compatibility window file, ``window.toml``, and its parser.

``floor`` is the oldest ``MAJOR.MINOR`` a peer may still speak; ``opening`` is the major
number master is opening, absent while no major is open. Both are TOML strings, so
``0.10`` never collapses to the float ``0.1``, and an unknown key is refused so a
misspelled ``opening`` cannot read as "master not open"."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Self

from packaging.version import InvalidVersion, Version

from blizzard.foundation.roles import dto
from blizzard.foundation.runner_version import VersionRefusalReason

_FLOOR = re.compile(r"\d+\.\d+")
_MAJOR = re.compile(r"\d+")
_KEYS = frozenset({"floor", "opening"})
_PACKAGED = "window.toml"

#: The first line whose runners report a version; a floor at or above it refuses a runner that reports none.
HANDSHAKE_SINCE = "0.3"


class VersionWindowError(ValueError):
    """The window file is missing, unparseable, or malformed; the message names the file."""


@dto
@dataclass(frozen=True)
class VersionWindow:
    """The parsed window: the floor (a major and minor number) and, while a major is opening, its number."""

    floor: str
    opening: str | None = None

    @classmethod
    def load(cls, source: Path | None = None) -> Self:
        """Parse ``source``, or the packaged ``window.toml`` when none is given."""
        if source is None:
            source = Path(str(files("blizzard.wire").joinpath(_PACKAGED)))
        return cls.parse(_read(source), str(source))

    @classmethod
    def parse(cls, text: str, source: str) -> Self:
        """Parse window ``text`` read from ``source``, which labels every refusal — the same rule as ``load``."""
        floor, opening = _parse(text, source)
        return cls(floor=floor, opening=opening)


def _read(source: Path) -> str:
    try:
        return source.read_text()
    except OSError as error:
        raise VersionWindowError(f"{source}: cannot read the window file: {error}") from error


def _parse(text: str, source: str) -> tuple[str, str | None]:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise VersionWindowError(f"{source}: not valid TOML: {error}") from error
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise VersionWindowError(f"{source}: unknown key(s) {unknown}; the window has only {sorted(_KEYS)}")
    floor = _text(source, raw, "floor", _FLOOR, "MAJOR.MINOR")
    if floor is None:
        raise VersionWindowError(f"{source}: `floor` is required")
    return floor, _text(source, raw, "opening", _MAJOR, "a major number")


def _text(source: str, raw: dict[str, Any], key: str, shape: re.Pattern[str], described: str) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str) or not shape.fullmatch(value):
        raise VersionWindowError(f"{source}: `{key}` must be a quoted string of {described}, got {value!r}")
    return value


@dto
@dataclass(frozen=True)
class Admission:
    """The window's judgement of one runner's version: ``reason`` is ``None`` when it is admitted."""

    reason: VersionRefusalReason | None
    #: The version as reported; ``None`` when absent or unparseable.
    runner_version: str | None
    hub_version: str
    floor: str
    #: The newest line the hub admits — its own.
    ceiling: str

    def admitted(self) -> bool:
        return self.reason is None

    def detail(self) -> str | None:
        """The operator-facing sentence naming what to upgrade and to which range; ``None`` when admitted."""
        if self.reason is None:
            return None
        return refusal_detail(self.reason, self.runner_version, self.hub_version, self.floor, self.ceiling)


def admits(hub: str, runner: str | None, window: VersionWindow) -> Admission:
    """Admit a runner whose ``MAJOR.MINOR`` line is between ``window``'s floor and the hub's own, inclusive."""
    ceiling = line_of(hub)
    runner_line = _parsed_line(runner) if runner is not None else None
    reported = runner if runner_line is not None else None
    if runner_line is None:
        refused = _line(window.floor) >= _line(HANDSHAKE_SINCE)
        reason = VersionRefusalReason.UNVERSIONED if refused else None
    elif runner_line < _line(window.floor):
        reason = VersionRefusalReason.TOO_OLD
    elif runner_line > _line(ceiling):
        reason = VersionRefusalReason.TOO_NEW
    else:
        reason = None
    return Admission(reason=reason, runner_version=reported, hub_version=hub, floor=window.floor, ceiling=ceiling)


def refusal_detail(
    reason: VersionRefusalReason, runner_version: str | None, hub_version: str, floor: str, ceiling: str
) -> str:
    if reason is VersionRefusalReason.TOO_NEW:
        return f"runner {runner_version} is newer than this hub, {hub_version}; upgrade the hub first"
    upgrade = f"upgrade the runner host to {floor} or later, up to {ceiling}"
    if reason is VersionRefusalReason.TOO_OLD:
        return f"runner {runner_version} is older than this hub's oldest supported line, {floor}; {upgrade}"
    return f"runner reports no version, which this hub, {hub_version}, no longer admits; {upgrade}"


def line_of(version: str) -> str:
    """The ``MAJOR.MINOR`` line ``version`` belongs to; raises ``InvalidVersion`` when it does not parse."""
    major, minor = _release_line(Version(version))
    return f"{major}.{minor}"


def check_own_floor(hub: str, window: VersionWindow) -> None:
    """Refuse a hub at ``hub`` whose line is below ``window``'s floor — it would refuse every runner."""
    if _release_line(Version(hub)) < _line(window.floor):
        raise VersionWindowError(
            f"this hub's version, {hub}, is below its window's floor, {window.floor}; it would refuse every runner"
        )


def _parsed_line(version: str) -> tuple[int, int] | None:
    try:
        return _release_line(Version(version))
    except InvalidVersion:
        return None


def _release_line(version: Version) -> tuple[int, int]:
    release = (*version.release, 0)
    return release[0], release[1]


def _line(major_minor: str) -> tuple[int, int]:
    major, minor = major_minor.split(".")
    return int(major), int(minor)
