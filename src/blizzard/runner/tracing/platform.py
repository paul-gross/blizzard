"""The runner's platform-span names: the scope its tick spans are opened under and the tick-step attribute, and the
stamps a runner's and its host's spans carry.

Contract: ``blizzard-product:/delivered/tracing/platform-spans/spec/instrumentation.md`` §Attributes. Kept apart from
``attributes`` so the fleet-span dictionary's declared set does not absorb them."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.platform_tracing.handle import SpanStamp
from blizzard.runner.hub.identity import ICurrentRunnerIdentity
from blizzard.runner.tracing.attributes import RUNNER_ID, RUNNER_NAME, TENANT, TENANT_NAME

PLATFORM_INSTRUMENTATION_SCOPE = "blizzard.runner.platform"
PLATFORM_INSTRUMENTATION_SCOPE_VERSION = "1"

TICK_STEP = "blizzard.tick.step"


def identity_stamp(identity: ICurrentRunnerIdentity) -> SpanStamp:
    """What each of the runner's platform spans carries: the id and name of its latest registration and the
    tenant its hub answered with, read as the span starts — so a rename by restart shows from that
    registration on. The tenant rides
    only once a hub that names it has answered."""

    def stamp() -> Mapping[str, str] | None:
        current = identity.current()
        if current is None:
            return None
        stamped = {RUNNER_ID: current.runner_id, RUNNER_NAME: current.runner_name}
        tenant = {TENANT: current.tenant_id, TENANT_NAME: current.tenant_name}
        return stamped | {key: value for key, value in tenant.items() if value is not None}

    return stamp


def host_stamp() -> SpanStamp:
    """What each of a host's platform spans carries unless what opened it stamps it as one of its runners — a
    runner's tick through :meth:`~blizzard.foundation.platform_tracing.handle.IPlatformTracing.stamped`, a request
    under a runner's prefix through ``stamping`` — or it inherits such a stamp from its parent: no runner identity,
    and the span is exported."""

    def stamp() -> Mapping[str, str] | None:
        return {}

    return stamp
