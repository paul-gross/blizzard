"""``blizzard hub login``'s local session-token store (issue #96) — CLI-client state, not
hub daemon state: session bearers keyed by hub base URL under the user config dir,
owner-only (``0600``; parent dir ``0700``)."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import platformdirs

_APP_NAME = "blizzard"


@dataclass(frozen=True)
class SessionFile:
    """The CLI's ``sessions.json`` — one session bearer per hub, so more than one may be held at once."""

    path: Path

    @classmethod
    def of(cls) -> SessionFile:
        return cls(Path(platformdirs.user_config_dir(_APP_NAME)) / "sessions.json")

    def load(self, hub_url: str) -> str | None:
        return self._all().get(hub_url)

    def save(self, hub_url: str, token: str) -> None:
        sessions = self._all()
        sessions[hub_url] = token
        self._write(sessions)

    def delete(self, hub_url: str) -> None:
        sessions = self._all()
        if hub_url not in sessions:
            return
        del sessions[hub_url]
        if sessions:
            self._write(sessions)
        else:
            self.path.unlink(missing_ok=True)

    def _all(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, sessions: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, stat.S_IRWXU)
        self.path.write_text(json.dumps(sessions))
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
