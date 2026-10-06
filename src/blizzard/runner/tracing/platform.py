"""The runner's platform-span names: the scope its tick spans are opened under and the tick-step attribute.

Contract: ``blizzard-product:/delivered/tracing/platform-spans/spec/instrumentation.md`` §Attributes. Kept apart from
``attributes`` so the fleet-span dictionary's declared set does not absorb them."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.platform_tracing.handle import SpanStamp
from blizzard.runner.hub.identity import ICurrentRunnerIdentity
from blizzard.runner.tracing.attributes import RUNNER_ID, RUNNER_NAME

PLATFORM_INSTRUMENTATION_SCOPE = "blizzard.runner.platform"
PLATFORM_INSTRUMENTATION_SCOPE_VERSION = "1"

TICK_STEP = "blizzard.tick.step"


def identity_stamp(identity: ICurrentRunnerIdentity) -> SpanStamp:
    """What each of the runner's platform spans carries: the id and name of its latest registration, read
    as the span starts — so a rename by restart shows from that registration on — and nothing before the
    first, so no span is exported until then."""

    def stamp() -> Mapping[str, str] | None:
        current = identity.current()
        return None if current is None else {RUNNER_ID: current.runner_id, RUNNER_NAME: current.runner_name}

    return stamp
