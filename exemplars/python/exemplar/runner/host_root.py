"""Stand-ins for the runner host's infrastructure and serving roots, with the disk area added."""

# exemplar: bzh:dependency-injection — production adds the area's registration to its host infrastructure.

from __future__ import annotations

from blizzard.foundation.clock import IClock, IMonotonicClock
from blizzard.foundation.registry.scopes import OwnedChild
from blizzard.runner import wiring as runner_wiring
from blizzard.runner.composition import build_host_process
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_root import OpenedHost, open_host, open_host_infrastructure
from blizzard.runner.hub.host_options import HostHubOptions
from blizzard.runner.loop.host_tick import HostTickInForce
from blizzard.runner.tracing.host_options import HostTracingOptions
from exemplar.runner.disk import wiring as disk


def open_infrastructure(
    config: RunnerConfig,
    clock: IClock,
    monotonic_clock: IMonotonicClock,
    runtime: runner_wiring.HostRuntimeOptions,
    hub_options: HostHubOptions,
    tracing_options: HostTracingOptions,
    *,
    tick_in_force: HostTickInForce[SubscriptionDeclaration],
) -> OwnedChild:
    """Open the host infrastructure with the disk area over the caller's borrowed inputs."""
    return open_host_infrastructure(
        config,
        clock,
        monotonic_clock,
        runtime,
        hub_options,
        tracing_options,
        tick_in_force=tick_in_force,
        extend=disk.register,
    )


def open_serving(host_config: HostConfig) -> OpenedHost:
    """Open the host with the disk area and transfer its infrastructure to the serving root."""
    host = build_host_process(host_config, extend=disk.register)
    return open_host(host, host_config)
