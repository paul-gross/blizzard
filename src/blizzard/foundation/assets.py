"""Locating the wheel-embedded frontend assets.

The compiled Angular apps live under ``blizzard/static/<app>`` inside the
package, so they ship in the one wheel and are found the same way whether the
package is installed or run from a source checkout. Until a build fills these
directories, ``index.html`` is absent."""

from __future__ import annotations

from pathlib import Path

import blizzard

_STATIC_ROOT = Path(blizzard.__file__).resolve().parent / "static"


def frontend_dir(app_name: str) -> Path:
    """Return the embedded static-assets directory for ``app_name`` (``hub`` / ``runner``)."""
    return _STATIC_ROOT / app_name
