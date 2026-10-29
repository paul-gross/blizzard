"""Where the hub keeps its files under its runtime root.

Each path is named once and shared; ``docs/backup.md`` tells an operator which to protect."""

from __future__ import annotations

CONFIG_FILENAME = "blizzard-hub.toml"
DATA_DIRNAME = "data"
STORE_FILENAME = "hub.db"
#: Under the data directory: the IdP signing keypairs, populated once ``auth.mode = "oauth"`` is configured.
SIGNING_KEYS_PARTS = ("auth", "signing-keys")
#: Under the data directory: the keys that seal every stored secret.
SECRET_KEYS_PARTS = ("auth", "secret-keys")
#: Under the data directory: a scratch-clone directory nothing writes; it is reclaimable.
WORKDIRS_DIRNAME = "hub_workdirs"
