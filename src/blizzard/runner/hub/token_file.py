"""The runner's hub bearer token as its runtime dir's ``.env`` holds it.

``runner init`` writes the token the hub issues there, and every runner verb reads it back when the
process environment carries none. The file keeps systemd ``EnvironmentFile`` syntax — ``NAME=value``
lines, ``#`` and ``;`` comments, optionally quoted values, the last assignment winning — because an
installed runner's unit loads the same file, and the token's line is the only one ``init`` rewrites."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import collaborator

#: The file in a runner runtime dir that holds its hub token.
ENV_FILENAME = ".env"

_COMMENT_PREFIXES = ("#", ";")
_QUOTES = ('"', "'")
_OWNER_ONLY = 0o600
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
        token = ""
        for line in text.splitlines():
            assigned = _assignment(line)
            if assigned is not None and assigned[0] == self.token_env:
                token = assigned[1]
        return token

    def write(self, token: str) -> None:
        """Replace the token's assignment, keeping every other line as it was, through a temp file
        renamed over the original — a crash leaves the old file or the new one, never half of
        either. The file is owner-only (``0600``)."""
        lines = self.path.read_text().splitlines() if self.path.exists() else []
        kept: list[str] = []
        placed = False
        for line in lines:
            assigned = _assignment(line)
            if assigned is None or assigned[0] != self.token_env:
                kept.append(line)
            elif not placed:
                kept.append(f"{self.token_env}={token}")
                placed = True
        if not placed:
            kept.append(f"{self.token_env}={token}")
        fd, pending = tempfile.mkstemp(dir=self.path.parent, prefix=f"{ENV_FILENAME}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write("\n".join(kept) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
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
        self.path.chmod(_OWNER_ONLY)
        return True


def _assignment(line: str) -> tuple[str, str] | None:
    """A ``NAME=value`` line's name and unquoted value; ``None`` for a blank line, a comment, or
    anything else that assigns nothing."""
    stripped = line.strip()
    if not stripped or stripped.startswith(_COMMENT_PREFIXES) or "=" not in stripped:
        return None
    name, _, value = stripped.partition("=")
    value = value.strip()
    if len(value) >= 2 and value[0] in _QUOTES and value[-1] == value[0]:
        value = value[1:-1]
    return name.strip(), value
