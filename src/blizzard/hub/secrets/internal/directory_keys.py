"""The ``data/auth/secret-keys/`` key provider — laid out as ``signing-keys/`` is.

The directory is ``0700`` and every file ``0600``; ``meta.json`` names the ``current``
and ``previous`` generation ids, and each generation is ``<key_id>.key``. Generations
resolve from disk on every use, so a running hub follows an offline rotation. ``meta.json`` is
replaced whole, so a reader always sees a complete one."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path

from blizzard.hub.config import ConfigError
from blizzard.hub.domain.config.secrets import IHubKeyProvider, KeyGeneration
from blizzard.hub.secrets.internal.key_material import KEY_BYTES, generation_of

_META_FILENAME = "meta.json"
_KEY_SUFFIX = ".key"
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class DirectoryKeyProvider:
    """Mints the first generation on construction when ``meta.json`` is absent."""

    def __init__(self, keys_dir: Path) -> None:
        self._dir = keys_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._dir.chmod(_DIR_MODE)
        if not self._meta_path.exists():
            generation = self.mint()
            self._write_meta(current=generation.key_id, previous=None)

    @staticmethod
    def initialized(keys_dir: Path) -> bool:
        """Whether ``keys_dir`` holds a generation already — checked without constructing, which mints one."""
        return (keys_dir / _META_FILENAME).exists()

    def current(self) -> KeyGeneration:
        key_id = str(self._read_meta()["current"])
        generation = self.generation(key_id)
        if generation is None:
            raise ConfigError(f"the current secret key generation {key_id} is missing from {self._dir}")
        return generation

    def generation(self, key_id: str) -> KeyGeneration | None:
        path = self._dir / f"{key_id}{_KEY_SUFFIX}"
        if not path.is_file():
            return None
        generation = generation_of(path.read_bytes())
        return generation if generation.key_id == key_id else None

    def available_ids(self) -> frozenset[str]:
        return frozenset(
            path.stem for path in self._dir.glob(f"*{_KEY_SUFFIX}") if self.generation(path.stem) is not None
        )

    @property
    def _meta_path(self) -> Path:
        return self._dir / _META_FILENAME

    def mint(self) -> KeyGeneration:
        """Write a fresh generation's file without naming it current — readable at once,
        sealing nothing until :meth:`promote`."""
        generation = generation_of(secrets.token_bytes(KEY_BYTES))
        path = self._dir / f"{generation.key_id}{_KEY_SUFFIX}"
        path.touch(mode=_FILE_MODE)
        path.chmod(_FILE_MODE)
        path.write_bytes(generation.material)
        return generation

    def promote(self, key_id: str) -> None:
        """Name ``key_id`` current and demote the generation it replaces to ``previous``."""
        old = str(self._read_meta()["current"])
        self._write_meta(current=key_id, previous=old if old != key_id else self._read_meta().get("previous"))

    def prune(self, referenced: frozenset[str]) -> None:
        """Delete every generation file that is neither current, previous, nor referenced by a row."""
        meta = self._read_meta()
        keep = {str(meta["current"]), *referenced}
        if meta.get("previous"):
            keep.add(str(meta["previous"]))
        for path in self._dir.glob(f"*{_KEY_SUFFIX}"):
            if path.stem not in keep:
                path.unlink()

    def _read_meta(self) -> dict[str, str | None]:
        return json.loads(self._meta_path.read_text())

    def _write_meta(self, *, current: str, previous: str | None) -> None:
        fd, pending = tempfile.mkstemp(dir=self._dir, prefix=f"{_META_FILENAME}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(json.dumps({"current": current, "previous": previous}))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pending, self._meta_path)
        except BaseException:
            Path(pending).unlink(missing_ok=True)
            raise


def _conforms_directory_key_provider(x: DirectoryKeyProvider) -> IHubKeyProvider:
    return x
