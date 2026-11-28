"""Runner-owned host infrastructure registrations."""

from __future__ import annotations

import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import tzinfo

from blizzard.foundation.clock import IClock, IMonotonicClock, local_zone
from blizzard.foundation.flags import FlagRegistry, Flags, resolve_flags, warn_held_keys, warn_ignored_flags
from blizzard.foundation.logging import get_logger
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.foundation.roles import adapter_model
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.hub.host_options import HostHubOptions
from blizzard.runner.listeners import Listeners
from blizzard.runner.loop.host_tick import HostTickInForce, register_host_tick
from blizzard.runner.process.child_env import ToolProfileHolder
from blizzard.runner.tracing.host_options import HostTracingOptions


@adapter_model
@dataclass(frozen=True)
class HostRuntimeOptions:
    """Borrowed runtime values not owned by an individual concept."""

    environ: Mapping[str, str]
    events: EventBroker | None = None
    tcp_socket: socket.socket | None = None


def host_events(options: HostRuntimeOptions) -> EventBroker | None:
    return options.events


def host_zone(options: HostRuntimeOptions) -> tzinfo:
    return local_zone(options.environ)


def host_listeners(config: RunnerConfig, options: HostRuntimeOptions) -> Listeners:
    return Listeners.of(config, tcp=options.tcp_socket)


def host_tool_profile(config: RunnerConfig, options: HostRuntimeOptions) -> ToolProfileHolder:
    return ToolProfileHolder(config.tool_env(TracingSettings.of(options.environ)))


def host_flag_registry(config: RunnerConfig) -> FlagRegistry:
    return config.flag_registry


def host_flags(config: RunnerConfig) -> Flags:
    resolution = config.flag_resolution or resolve_flags(config.flag_registry, "runner", config.flags)
    log = get_logger("blizzard.runner")
    warn_ignored_flags(log, resolution)
    warn_held_keys(log, config.held_keys)
    return resolution.flags


def register_host_infrastructure(registry: Registry) -> None:
    registry.factory(host_events, lifetime=Lifetime.PROCESS)
    registry.factory(host_zone, lifetime=Lifetime.PROCESS)
    registry.factory(host_listeners, lifetime=Lifetime.PROCESS)
    registry.factory(host_tool_profile, lifetime=Lifetime.PROCESS)
    registry.factory(host_flag_registry, lifetime=Lifetime.PROCESS)
    registry.factory(host_flags, lifetime=Lifetime.PROCESS)


def register_infrastructure_contexts(registry: Registry) -> None:
    """The host scope's borrowed opening values."""
    for key in (RunnerConfig, IClock, IMonotonicClock, HostRuntimeOptions, HostHubOptions, HostTracingOptions):
        registry.context(port(key), lifetime=Lifetime.PROCESS)
    register_host_tick(registry, HostTickInForce[SubscriptionDeclaration])
