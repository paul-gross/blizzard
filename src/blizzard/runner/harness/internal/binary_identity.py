"""Filesystem binding for executable identity under the tool profile's PATH."""

from __future__ import annotations

import shutil
from pathlib import Path

from blizzard.runner.harness.binary_identity import BinaryIdentity
from blizzard.runner.process.child_env import ChildEnv


class ToolBinaryIdentity:
    """Resolve like the version probe and fingerprint the executable's resolved target."""

    def __init__(self, env: ChildEnv) -> None:
        self._env = env

    def identify(self, binary: str) -> BinaryIdentity | None:
        resolved = shutil.which(binary, path=self._env.variables.get("PATH", ""))
        if resolved is None:
            return None
        try:
            path = Path(resolved).resolve(strict=True)
            stat = path.stat()
        except OSError:
            return None
        return BinaryIdentity(path=path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
