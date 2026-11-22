"""Resolve one effective hub configuration for a request."""

from fastapi import Request

from blizzard.hub.api.resolution import request_resolver
from blizzard.hub.config import HubConfig
from blizzard.hub.settings.source import HubConfigSource


def request_config(request: Request) -> HubConfig:
    """The config the request reads, resolved once from the process's live config source."""
    cached: HubConfig | None = getattr(request.state, "hub_config", None)
    if cached is not None:
        return cached
    config = request_resolver(request).get(HubConfigSource).resolved()
    request.state.hub_config = config
    return config
