"""Where the hub keeps its files under its runtime root.

Named once so the config registry, the composition root and the secret-key provider agree on every path
``docs/backup.md`` tells an operator to protect."""

from __future__ import annotations

CONFIG_FILENAME = "blizzard-hub.toml"
DATA_DIRNAME = "data"
STORE_FILENAME = "hub.db"
#: Under the data directory: the IdP signing keypairs, populated once ``auth.mode = "oauth"`` is configured.
SIGNING_KEYS_PARTS = ("auth", "signing-keys")
#: Under the data directory: the keys that seal every stored secret.
SECRET_KEYS_PARTS = ("auth", "secret-keys")
#: Under the data directory: scratch clones a hub `run:` step once used; nothing writes it now, so it is reclaimable.
WORKDIRS_DIRNAME = "hub_workdirs"
