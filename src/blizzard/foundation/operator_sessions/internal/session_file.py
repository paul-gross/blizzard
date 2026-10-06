from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from blizzard.foundation.operator_sessions import IReadSessionStore, IWriteSessionStore
from blizzard.foundation.roles import collaborator

_APP_NAME = "blizzard"


@collaborator
@dataclass(frozen=True)
class SessionFile:
    """The CLI's ``sessions.json`` — one session bearer per hub, so more than one may be held at once."""

    path: Path

    @classmethod
    def of(cls) -> SessionFile:
        return cls(Path(platformdirs.user_config_dir(_APP_NAME)) / "sessions.json")

    def load(self, hub_url: str) -> str | None:
        return self._all().get(_key(hub_url))

    def save(self, hub_url: str, token: str) -> None:
        sessions = self._all()
        sessions[_key(hub_url)] = token
        self._write(sessions)

    def delete(self, hub_url: str) -> None:
        sessions = self._all()
        key = _key(hub_url)
        if key not in sessions:
            return
        del sessions[key]
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
        return {_key(url): token for url, token in data.items()} if isinstance(data, dict) else {}

    def _write(self, sessions: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, stat.S_IRWXU)
        self.path.write_text(json.dumps(sessions))
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)


def _key(hub_url: str) -> str:
    """One hub, one key: a stored or looked-up URL drops its trailing ``/``, so
    ``http://hub/`` and ``http://hub`` find the same session."""
    return hub_url.rstrip("/")


def _conforms_session_file_read(x: SessionFile) -> IReadSessionStore:
    return x


def _conforms_session_file_write(x: SessionFile) -> IWriteSessionStore:
    return x
