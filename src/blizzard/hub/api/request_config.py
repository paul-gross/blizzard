"""Resolve one effective hub configuration for a request."""

from fastapi import Request

from blizzard.hub.config import HubConfig


def request_config(request: Request) -> HubConfig:
    cached: HubConfig | None = getattr(request.state, "hub_config", None)
    if cached is not None:
        return cached
    settings = getattr(request.app.state, "hub_settings", None)
    if settings is None:
        config: HubConfig = request.app.state.config
        request.state.hub_config_revision = 0
    else:
        snapshot = settings.reader.snapshot()
        config = settings.footing.resolve(snapshot.entries)
        request.state.hub_config_revision = snapshot.revision
    request.state.hub_config = config
    return config
