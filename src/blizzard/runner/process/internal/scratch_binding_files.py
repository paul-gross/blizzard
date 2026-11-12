"""File adapter for :class:`~blizzard.runner.process.scratch_binding.ScratchBindingPort`.

The binding lives in one file under the host's ``data/`` directory; the root's
usability is proven by a probe file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

BINDING_FILENAME = "worker-scratch-root"


def _check_root(root: Path) -> None:
    with os.scandir(root):
        pass
    fd, name = tempfile.mkstemp(prefix=".scratch-probe-", dir=root)
    os.close(fd)
    os.unlink(name)


def _sync_root_path(root: Path) -> None:
    """Make every directory entry leading to the root durable before binding it.

    Sync existing ancestors too: an earlier attempt may have created them and
    crashed before syncing their parents.
    """
    for directory in reversed((root, *root.parents)):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class ScratchBindingFiles:
    def __init__(self, data_dir: Path) -> None:
        self._data = data_dir
        self._binding = data_dir / BINDING_FILENAME

    @property
    def location(self) -> str:
        return str(self._binding)

    def prepare(self, root: Path) -> None:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _check_root(root)
        _sync_root_path(root)

    def probe(self, root: Path) -> None:
        _check_root(root)

    def read_previous(self) -> Path | None:
        self._data.mkdir(parents=True, exist_ok=True)
        try:
            return Path(self._binding.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def publish(self, root: Path) -> None:
        fd, name = tempfile.mkstemp(prefix=f".{BINDING_FILENAME}.", dir=self._data)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(str(root))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._binding)
            dir_fd = os.open(self._data, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)
