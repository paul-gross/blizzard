"""Where the hub keeps its files under its runtime root.

Each path is named once and shared; ``docs/backup.md`` tells an operator which to protect."""

from __future__ import annotations

import shutil
from pathlib import Path

CONFIG_FILENAME = "blizzard-hub.toml"
DATA_DIRNAME = "data"
STORE_FILENAME = "hub.db"
#: Under the data directory: the IdP signing keypairs, populated once ``auth.mode = "oauth"`` is configured.
SIGNING_KEYS_PARTS = ("auth", "signing-keys")
#: Under the data directory: the keys that seal every stored secret.
SECRET_KEYS_PARTS = ("auth", "secret-keys")
#: Under the data directory: the retired scratch-clone directory a hub `run:` step used; the hub removes it on boot.
_RETIRED_WORKDIRS_DIRNAME = "hub_workdirs"


def remove_retired_workdirs(data_dir: Path) -> None:
    """Remove the retired scratch-clone directory under ``data_dir``.

    Idempotent: a missing path is a no-op, a symlink is unlinked and never followed, and a removal a crash interrupts
    is finished by the next boot."""
    path = data_dir / _RETIRED_WORKDIRS_DIRNAME
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
