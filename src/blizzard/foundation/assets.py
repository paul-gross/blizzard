"""Locating the wheel-embedded frontend assets (D-061).

The compiled Angular apps live under ``blizzard/static/<app>`` inside the
package, so they ship in the one wheel and are found the same way whether the
package is installed or run from a source checkout. CI (or a local
``npm run build``) fills these directories with the real build output; when no
build has run, ``index.html`` is absent and the daemon serves a runtime
placeholder (``blizzard.foundation.web.mount_web_app``), keeping the seam live.
"""

from __future__ import annotations

from pathlib import Path

import blizzard

_STATIC_ROOT = Path(blizzard.__file__).resolve().parent / "static"


def frontend_dir(app_name: str) -> Path:
    """Return the embedded static-assets directory for ``app_name`` (``hub`` / ``runner``)."""
    return _STATIC_ROOT / app_name
