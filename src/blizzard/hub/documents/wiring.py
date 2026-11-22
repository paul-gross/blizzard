"""The documents adapter's registrations: the config codec the hub's consumers decode and encode graphs through.

The codec is store-independent, so both the store-backed and the store-free graph declare it."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.hub.documents.codec import IConfigCodec
from blizzard.hub.documents.internal.yaml_codec import YAML_CODEC


def register(registry: Registry) -> None:
    """Declare the YAML binding as the process's config codec."""
    registry.factory(config_codec, lifetime=Lifetime.PROCESS)


def config_codec() -> IConfigCodec:
    """The YAML binding."""
    return YAML_CODEC
