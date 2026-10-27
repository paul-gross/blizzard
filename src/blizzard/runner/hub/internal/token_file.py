"""The ``.env`` file driver behind ``ITokenWriter`` and ``IHubTokenFile``: reads, rewrites, and
restricts the token's file."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import collaborator
from blizzard.runner.hub.bootstrap import ITokenWriter
from blizzard.runner.hub.held_token import IHubTokenFile
from blizzard.runner.hub.token_file import ENV_FILE_MODE, ENV_FILENAME, assigned_token, with_token, without_token

_OTHERS_ACCESS = stat.S_IRWXG | stat.S_IRWXO


@collaborator
@dataclass(frozen=True)
class HubTokenFile:
    """The ``token_env`` assignment in one runtime dir's ``.env``."""

    path: Path
    token_env: str

    @classmethod
    def of(cls, root: Path, token_env: str) -> HubTokenFile:
        return cls(root / ENV_FILENAME, token_env)

    def held(self) -> str:
        """The token the file assigns, or ``""`` when the file or the assignment is absent."""
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return ""
        return assigned_token(text, self.token_env)

    def write(self, token: str) -> None:
        """Replace the token's assignment, keeping every other line as it was, through a temp file
        renamed over the original — a crash leaves the old file or the new one, never half of
        either. The file is owner-only (``0600``)."""
        self._replace(with_token(self.path.read_text() if self.path.exists() else "", self.token_env, token))

    def remove(self) -> None:
        """Remove the token's every assignment, keeping every other line as it was, as :meth:`write` replaces it; a
        file or assignment already absent is left as it is."""
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return
        edited = without_token(text, self.token_env)
        if edited.rstrip("\n") != text.rstrip("\n"):
            self._replace(edited)

    def _replace(self, text: str) -> None:
        fd, pending = tempfile.mkstemp(dir=self.path.parent, prefix=f"{ENV_FILENAME}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(pending, ENV_FILE_MODE)
            os.replace(pending, self.path)
        except BaseException:
            Path(pending).unlink(missing_ok=True)
            raise

    def restrict(self) -> bool:
        """Make a file holding the token owner-only (``0600``) when group or other may access it, its
        bytes left as they are; ``True`` when its mode changed. Raises ``OSError`` when it cannot —
        another account owns it."""
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
        except FileNotFoundError:
            return False
        if not mode & _OTHERS_ACCESS or not self.held():
            return False
        self.path.chmod(ENV_FILE_MODE)
        return True


def _conforms_token_writer(x: HubTokenFile) -> ITokenWriter:
    return x


def _conforms_hub_token_file(x: HubTokenFile) -> IHubTokenFile:
    return x
