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

from blizzard.foundation.roles import dto

_FLOOR = re.compile(r"\d+\.\d+")
_MAJOR = re.compile(r"\d+")
_KEYS = frozenset({"floor", "opening"})
_PACKAGED = "window.toml"


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
        floor, opening = _parse(source)
        return cls(floor=floor, opening=opening)


def _parse(source: Path) -> tuple[str, str | None]:
    try:
        raw = tomllib.loads(source.read_text())
    except OSError as error:
        raise VersionWindowError(f"{source}: cannot read the window file: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise VersionWindowError(f"{source}: not valid TOML: {error}") from error
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise VersionWindowError(f"{source}: unknown key(s) {unknown}; the window has only {sorted(_KEYS)}")
    floor = _text(source, raw, "floor", _FLOOR, "MAJOR.MINOR")
    if floor is None:
        raise VersionWindowError(f"{source}: `floor` is required")
    return floor, _text(source, raw, "opening", _MAJOR, "a major number")


def _text(source: Path, raw: dict[str, Any], key: str, shape: re.Pattern[str], described: str) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str) or not shape.fullmatch(value):
        raise VersionWindowError(f"{source}: `{key}` must be a quoted string of {described}, got {value!r}")
    return value
