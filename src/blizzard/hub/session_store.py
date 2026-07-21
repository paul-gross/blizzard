"""``blizzard hub login``'s local session-token store (issue #96).

CLI-client state, not hub daemon state: the token the CLI stores here is a bearer the
human-plane edge (``hub/api/auth_session.py``) resolves the same way a browser's
cookie resolves, but the CLI has no cookie jar of its own — one ``sessions.json``
under the user config dir, keyed by hub base URL (a CLI can hold sessions for more
than one hub), owner-only (``0600``; parent dir ``0700``).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import platformdirs

_APP_NAME = "blizzard"


def _sessions_path() -> Path:
    return Path(platformdirs.user_config_dir(_APP_NAME)) / "sessions.json"


def _load_all(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(path: Path, sessions: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)
    path.write_text(json.dumps(sessions))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load_session(hub_url: str) -> str | None:
    """The stored session token for ``hub_url``, or ``None`` when none is stored."""
    return _load_all(_sessions_path()).get(hub_url)


def save_session(hub_url: str, token: str) -> None:
    """Persist ``token`` for ``hub_url`` — owner-only permissions on both the file
    (``0600``) and its parent directory (``0700``)."""
    path = _sessions_path()
    sessions = _load_all(path)
    sessions[hub_url] = token
    _write_all(path, sessions)


def delete_session(hub_url: str) -> None:
    """Remove the stored session for ``hub_url``, if any — a no-op when absent."""
    path = _sessions_path()
    sessions = _load_all(path)
    if hub_url not in sessions:
        return
    del sessions[hub_url]
    if sessions:
        _write_all(path, sessions)
    else:
        path.unlink(missing_ok=True)
