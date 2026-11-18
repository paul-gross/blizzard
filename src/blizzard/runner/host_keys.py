"""The host's key — the generations its runners' hub tokens are sealed under.

The key is a process setting (``bzh:config-read-on-use`` Scope): ``BZ_HOST_SECRET_KEY``, when set, is the only
source, with ``BZ_HOST_SECRET_KEY_PREVIOUS`` readable beside it for a rotation; otherwise the host directory's
``data/secret-keys/`` holds the generations. A verb that seals mints the directory's first generation; a read-only
verb never does. A verb sealing on a stopped host resolves the key as its service would, so it refuses an environment
the host's rows show is not the service's."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.secrets.factories import key_exists, key_provider
from blizzard.foundation.secrets.key_variables import KeyVariables
from blizzard.foundation.secrets.sealing import IRotatableKeyProvider
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_layout import HostLayout

ENV_HOST_SECRET_KEY = "BZ_HOST_SECRET_KEY"
ENV_HOST_SECRET_KEY_PREVIOUS = "BZ_HOST_SECRET_KEY_PREVIOUS"
HOST_KEY_VARIABLES = KeyVariables(current=ENV_HOST_SECRET_KEY, previous=ENV_HOST_SECRET_KEY_PREVIOUS)


def host_key_provider(environ: Mapping[str, str], layout: HostLayout, *, mint: bool) -> IRotatableKeyProvider | None:
    """The env source when ``BZ_HOST_SECRET_KEY`` is set, else the directory source. With ``mint``, the directory's
    first generation is minted when none exists; without it, ``None`` answers a host that holds no key yet. A
    malformed variable raises :class:`ConfigError`."""
    if not mint and not key_exists(environ, keys_dir=layout.secret_keys_dir, variables=HOST_KEY_VARIABLES):
        return None
    return _provider(environ, layout)


def sealing_key_provider(
    environ: Mapping[str, str], layout: HostLayout, *, sealed_under: frozenset[str]
) -> IRotatableKeyProvider:
    """The key a verb seals under, resolved as :func:`host_key_provider` with ``mint`` resolves it. Raises
    :class:`ConfigError` when ``BZ_HOST_SECRET_KEY`` is set but a generation in ``sealed_under`` — those the host's
    rows are sealed under — is in its ``data/secret-keys/`` instead: the variable is then not the host's key."""
    keys = _provider(environ, layout)
    stranded = sealed_under - keys.available_ids()
    if (
        stranded
        and environ.get(ENV_HOST_SECRET_KEY)
        and key_exists({}, keys_dir=layout.secret_keys_dir, variables=HOST_KEY_VARIABLES)
        and stranded & _provider({}, layout).available_ids()
    ):
        raise ConfigError(
            f"{ENV_HOST_SECRET_KEY} is set, but this host's hub tokens are sealed under the key in "
            f"{layout.secret_keys_dir}, so it is not the host's key; run this as the host's service runs, with "
            f"{ENV_HOST_SECRET_KEY} unset or set to the service's own"
        )
    return keys


def key_source(environ: Mapping[str, str], layout: HostLayout) -> str:
    """Where the host's key resolves from, in words."""
    return f"${ENV_HOST_SECRET_KEY}" if environ.get(ENV_HOST_SECRET_KEY) else str(layout.secret_keys_dir)


def _provider(environ: Mapping[str, str], layout: HostLayout) -> IRotatableKeyProvider:
    return key_provider(
        environ, keys_dir=layout.secret_keys_dir, variables=HOST_KEY_VARIABLES, config_error=ConfigError
    )
