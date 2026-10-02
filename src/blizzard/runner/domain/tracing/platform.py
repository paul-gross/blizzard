"""The runner's platform-span names: the scope its tick spans are opened under and the tick-step attribute.

Contract: ``blizzard-product:/plans/tracing/platform-spans/spec/instrumentation.md`` §Attributes. Kept apart from
``attributes`` so the fleet-span dictionary's declared set does not absorb them."""

from __future__ import annotations

PLATFORM_INSTRUMENTATION_SCOPE = "blizzard.runner.platform"
PLATFORM_INSTRUMENTATION_SCOPE_VERSION = "1"

TICK_STEP = "blizzard.tick.step"
