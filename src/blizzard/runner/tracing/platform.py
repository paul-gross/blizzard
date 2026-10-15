"""The runner's platform-span names: the scope its tick spans are opened under and the tick-step attribute, and the
per-runner context the host's spans are stamped from.

Contract: ``blizzard-product:/delivered/tracing/platform-spans/spec/instrumentation.md`` §Attributes. Kept apart from
``attributes`` so the fleet-span dictionary's declared set does not absorb them."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from blizzard.foundation.platform_tracing.handle import SpanStamp
from blizzard.runner.hub.identity import ICurrentRunnerIdentity, RunnerIdentity
from blizzard.runner.tracing.attributes import RUNNER_ID, RUNNER_NAME, TENANT, TENANT_NAME

PLATFORM_INSTRUMENTATION_SCOPE = "blizzard.runner.platform"
PLATFORM_INSTRUMENTATION_SCOPE_VERSION = "1"

TICK_STEP = "blizzard.tick.step"


def identity_stamp(identity: ICurrentRunnerIdentity) -> SpanStamp:
    """What each of the runner's platform spans carries: the id and name of its latest registration and the
    tenant its hub answered with, read as the span starts — so a rename by restart shows from that
    registration on — and nothing before the first, so no span is exported until then. The tenant rides
    only once a hub that names it has answered."""

    def stamp() -> Mapping[str, str] | None:
        current = identity.current()
        if current is None:
            return None
        stamped = {RUNNER_ID: current.runner_id, RUNNER_NAME: current.runner_name}
        tenant = {TENANT: current.tenant_id, TENANT_NAME: current.tenant_name}
        return stamped | {key: value for key, value in tenant.items() if value is not None}

    return stamp


#: The runner whose work the current thread or request does — unset outside any runner's.
_CURRENT_RUNNER: ContextVar[ICurrentRunnerIdentity | None] = ContextVar("blizzard_current_runner", default=None)


@contextmanager
def stamping_as(identity: ICurrentRunnerIdentity) -> Iterator[None]:
    """Stamp the platform spans opened inside as the runner ``identity`` answers for — a runner's tick thread and a
    request under its prefix."""
    token = _CURRENT_RUNNER.set(identity)
    try:
        yield
    finally:
        _CURRENT_RUNNER.reset(token)


class CurrentRunnerIdentity:
    """The :class:`ICurrentRunnerIdentity` of whichever runner's context the caller runs in, ``None`` outside any."""

    def current(self) -> RunnerIdentity | None:
        identity = _CURRENT_RUNNER.get()
        return identity.current() if identity is not None else None


def runner_context_stamp() -> SpanStamp:
    """What each of the host's platform spans carries, by the runner whose context opened it: inside a runner's,
    :func:`identity_stamp`'s answer for it — nothing exported before its first registration; outside any (the host
    tick, the host's own routes), no runner identity at all, and the span is exported."""

    def stamp() -> Mapping[str, str] | None:
        identity = _CURRENT_RUNNER.get()
        if identity is None:
            return {}
        return identity_stamp(identity)()

    return stamp
