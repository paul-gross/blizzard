"""The inputs a production root reads and hands to each daemon's config loader, read once for the suite, and the
loaders, runtimes, and apps built over them — so no test reads the packaged registry itself. Every in-process read is
strict, as the suite's spawned daemons are through the environment."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.config_keys import ConfigRegistry
from blizzard.foundation.flags import FlagRegistry, Flags, resolve_flags
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import HubConfig
from blizzard.runner import app as runner_app
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.host_config import HostConfig

#: The registry packaged with the wheel, as every root reads it.
FLAG_REGISTRY: Final = packaged_flag_registry()
#: Each daemon's flags at the packaged defaults, with no overrides.
HUB_FLAGS: Final[Flags] = resolve_flags(FLAG_REGISTRY, "hub", {}).flags
RUNNER_FLAGS: Final[Flags] = resolve_flags(FLAG_REGISTRY, "runner", {}).flags
#: Whether the suite's in-process config reads are strict.
STRICT: Final = True


def load_hub_config(
    root: Path,
    *,
    host: str | None = None,
    port: int | None = None,
    allow_external_db: bool = False,
    flag_registry: FlagRegistry = FLAG_REGISTRY,
    strict: bool = STRICT,
    key_registry: ConfigRegistry | None = None,
) -> HubConfig:
    """:meth:`HubConfig.load` with the suite's inputs."""
    return HubConfig.load(
        root,
        host=host,
        port=port,
        allow_external_db=allow_external_db,
        flag_registry=flag_registry,
        strict=strict,
        key_registry=key_registry,
    )


def load_host_config(
    root: Path,
    *,
    host: str | None = None,
    port: int | None = None,
    flag_registry: FlagRegistry = FLAG_REGISTRY,
    strict: bool = STRICT,
    key_registry: ConfigRegistry | None = None,
) -> HostConfig:
    """:meth:`HostConfig.load` with the suite's inputs."""
    return HostConfig.load(
        root, host=host, port=port, flag_registry=flag_registry, strict=strict, key_registry=key_registry
    )


def load_host_config_for_store(root: Path, *, host: str | None = None, port: int | None = None) -> HostConfig:
    """:meth:`HostConfig.load_for_store` with the suite's inputs."""
    return HostConfig.load_for_store(root, host=host, port=port, flag_registry=FLAG_REGISTRY, strict=STRICT)


def hub_runtime_of(root: Path, *, allow_external_db: bool = False) -> hub_runtime.Runtime:
    """The hub runtime at ``root`` with the suite's inputs."""
    return hub_runtime.Runtime(root, SystemClock(), allow_external_db, flag_registry=FLAG_REGISTRY, strict=STRICT)


def init_hub(root: Path, *, allow_external_db: bool = False) -> HubConfig:
    """:func:`blizzard.hub.runtime.init_environment` with the suite's inputs."""
    return hub_runtime.init_environment(
        root, allow_external_db=allow_external_db, flag_registry=FLAG_REGISTRY, strict=STRICT
    )


def migrate_hub(root: Path, *, down: str | None = None, allow_external_db: bool = False) -> None:
    """:func:`blizzard.hub.runtime.migrate` with the suite's inputs."""
    hub_runtime.migrate(
        root, down=down, allow_external_db=allow_external_db, flag_registry=FLAG_REGISTRY, strict=STRICT
    )


def host_runtime_of(
    root: Path,
    *,
    hub_url: str | None = None,
    projects: tuple[str, ...] | None = None,
    holder: Callable[[Path], str | None] | None = None,
) -> runner_runtime.Runtime:
    """The host runtime at ``root`` with the suite's inputs; ``holder`` defaults to the runtime's own."""
    if holder is None:
        return runner_runtime.Runtime(
            root, hub_url=hub_url, projects=projects, flag_registry=FLAG_REGISTRY, strict=STRICT
        )
    return runner_runtime.Runtime(
        root, hub_url=hub_url, projects=projects, holder=holder, flag_registry=FLAG_REGISTRY, strict=STRICT
    )


def init_host(
    root: Path,
    *,
    hub_url: str | None = None,
    projects: tuple[str, ...] | None = None,
    holder: Callable[[Path], str | None] | None = None,
) -> HostConfig:
    """:func:`blizzard.runner.runtime.init_environment` with the suite's inputs."""
    return host_runtime_of(root, hub_url=hub_url, projects=projects, holder=holder).init()


def migrate_host(root: Path, *, down: str | None = None) -> None:
    """:func:`blizzard.runner.runtime.migrate` with the suite's inputs."""
    host_runtime_of(root).migrate(down=down)


def create_hub_app(config: HubConfig, **kwargs: Any) -> FastAPI:
    """:func:`blizzard.hub.app.create_app`, its flags the packaged defaults unless ``flags`` is given."""
    if kwargs.get("flags") is None:
        kwargs["flags"] = HUB_FLAGS
    return hub_app.create_app(config, **kwargs)


def create_runner_app(config: Any, **kwargs: Any) -> FastAPI:
    """:func:`blizzard.runner.app.create_app`, its flags the packaged defaults unless ``flags`` is given."""
    if kwargs.get("flags") is None:
        kwargs["flags"] = RUNNER_FLAGS
    return runner_app.create_app(config, **kwargs)


def export_hub_app() -> FastAPI:
    """:func:`blizzard.hub.app.create_app_for_export` over the packaged registry."""
    return hub_app.create_app_for_export(FLAG_REGISTRY)


def export_runner_app() -> FastAPI:
    """:func:`blizzard.runner.app.create_app_for_export` over the packaged registry."""
    return runner_app.create_app_for_export(FLAG_REGISTRY)
