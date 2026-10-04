"""The ``data/auth/secret-keys/`` key provider — laid out as ``signing-keys/`` is.

The directory is ``0700`` and every file ``0600``; ``meta.json`` names the ``current``
and ``previous`` generation ids, and each generation is ``<key_id>.key``. Generations
resolve from disk on every use, so a running hub follows an offline rotation."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from blizzard.hub.config import ConfigError
from blizzard.hub.domain.secrets import IHubKeyProvider, KeyGeneration
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
            generation = self._mint()
            self._write_meta(current=generation.key_id, previous=None)

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

    def _mint(self) -> KeyGeneration:
        generation = generation_of(secrets.token_bytes(KEY_BYTES))
        path = self._dir / f"{generation.key_id}{_KEY_SUFFIX}"
        path.touch(mode=_FILE_MODE)
        path.chmod(_FILE_MODE)
        path.write_bytes(generation.material)
        return generation

    def _read_meta(self) -> dict[str, str | None]:
        return json.loads(self._meta_path.read_text())

    def _write_meta(self, *, current: str, previous: str | None) -> None:
        self._meta_path.write_text(json.dumps({"current": current, "previous": previous}))
        self._meta_path.chmod(_FILE_MODE)


def _conforms_directory_key_provider(x: DirectoryKeyProvider) -> IHubKeyProvider:
    return x
