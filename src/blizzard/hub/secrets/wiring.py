"""The hub's secret sealing registrations: the process key provider and the cipher sealing under it.

The key provider is a process setting (``bzh:config-read-on-use`` Scope) read from the borrowed environment and the boot
config's data directory; resolving it mints the first key generation when no key source exists yet. The store-free graph
declares each registration absent, so a route that reaches one answers the store's unavailability."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.foundation.secrets.factories import secret_cipher
from blizzard.foundation.secrets.sealing import IKeyProvider, IRotatableKeyProvider, ISecretCipher
from blizzard.hub.config import HubConfig
from blizzard.hub.scopes.environ import HubEnviron
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def key_provider(environ: HubEnviron, config: HubConfig) -> IRotatableKeyProvider:
    """The hub's rotatable key source."""
    return hub_key_provider(environ.values, data_dir=config.data_dir)


def register(registry: Registry) -> None:
    """Declare the key provider, its read-only port, and the cipher over it."""
    keys = registry.factory(key_provider, lifetime=Lifetime.PROCESS)
    registry.alias(port(IKeyProvider), keys)
    registry.factory(secret_cipher, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the key provider, its port and the cipher absent, for the store-free graph."""
    for missing in (IRotatableKeyProvider, IKeyProvider, ISecretCipher):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
