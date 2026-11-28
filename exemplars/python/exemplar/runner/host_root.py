"""A stand-in for the runner host's root: the host's own registration, with the disk area added.

It opens the root over a host its caller built and runs no runner, so the served app over it has only host routes."""

# exemplar: bzh:dependency-injection — production adds the area's ``register`` call to ``register_host`` itself.

from __future__ import annotations

from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.runner.composition import HostProcess
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_root import OpenedHost, open_host, register_host
from exemplar.runner.disk import wiring as disk


def open_serving(host: HostProcess, host_config: HostConfig) -> OpenedHost:
    """The host root over ``host`` with the disk area, opened for a served app; closing it closes the root, then
    ``host``."""
    registry = LegacyRegistry()
    register_host(registry)
    disk.register(registry)
    return open_host(host, host_config, registry=registry)
