"""Locating the wheel-embedded frontend assets.

The compiled Angular apps live under ``blizzard/static/<app>`` inside the
package, so they ship in the one wheel and are found the same way whether the
package is installed or run from a source checkout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import blizzard

_STATIC_ROOT = Path(blizzard.__file__).resolve().parent / "static"


@dataclass(frozen=True)
class EmbeddedFrontend:
    """The wheel-embedded static-assets directory for one compiled app."""

    app: str

    @property
    def directory(self) -> Path:
        return _STATIC_ROOT / self.app
