"""The effective configuration bound to a request or pass."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from blizzard.hub.config import HubConfig

_current: ContextVar[HubConfig | None] = ContextVar("hub_pass_config", default=None)


def current_pass_config() -> HubConfig | None:
    return _current.get()


@contextmanager
def bind_pass_config(config: HubConfig) -> Generator[None]:
    token = _current.set(config)
    try:
        yield
    finally:
        _current.reset(token)
