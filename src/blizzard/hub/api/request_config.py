"""Resolve one effective hub configuration for a request."""

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import Request

from blizzard.hub.auth.throttle import LiveIpThrottle
from blizzard.hub.config import HubConfig
from blizzard.hub.settings.source import HubConfigSource


@inject_sync
def request_config(request: Request, source: FromDishka[HubConfigSource]) -> HubConfig:
    """The config the request reads, resolved once from the process's live config source."""
    return config_for_request(request, source)


def config_for_request(request: Request, source: HubConfigSource) -> HubConfig:
    """Resolve and cache the config using an explicitly supplied source."""
    cached: HubConfig | None = getattr(request.state, "hub_config", None)
    if cached is not None:
        return cached
    config = source.resolved()
    request.state.hub_config = config
    return config


@inject_sync
def request_config_services(
    source: FromDishka[HubConfigSource], throttle: FromDishka[LiveIpThrottle | None]
) -> tuple[HubConfigSource, LiveIpThrottle | None]:
    """The config source and optional throttle used by the request's config binding."""
    return source, throttle
