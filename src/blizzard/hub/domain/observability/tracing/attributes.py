"""The hub's own trace attribute names, scope and service default; names the runner also emits live in
``blizzard.foundation.trace_attributes``.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md`` §Attributes and §GenAI usage."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation import trace_attributes as shared

INSTRUMENTATION_SCOPE = "blizzard.hub.fleet_spans"
INSTRUMENTATION_SCOPE_VERSION = "3"
#: The scope the hub's own platform spans (sweep roots) are opened under.
PLATFORM_INSTRUMENTATION_SCOPE = "blizzard.hub.platform"
PLATFORM_INSTRUMENTATION_SCOPE_VERSION = "1"
DEFAULT_SERVICE_NAME = "blizzard-hub"
#: The service name the lifetime trace's spans leave under, whatever the environment names.
CHUNK_SERVICE_NAME = "blizzard-chunk"

# Dimensions
STEP_OUTCOME = "blizzard.step.outcome"
STEP_CHOICE = "blizzard.step.choice"
STEP_TO_NODE_NAME = "blizzard.step.to_node.name"
STEP_PRECEDED_BY = "blizzard.step.preceded_by"
RUNNER_ID = "blizzard.runner.id"
RUNNER_NAME = "blizzard.runner.name"
STEP_MODELS = "blizzard.step.models"
BOUNCE_CAUSE = "blizzard.bounce.cause"
ASK_ANSWERED = "blizzard.ask.answered"
CLOCK_SKEW = "blizzard.clock_skew"
LINK_REASON = "blizzard.link.reason"
STEP_KIND = "blizzard.step.kind"

# Dimensions and measures — the chunk roots only
CHUNK_OUTCOME = "blizzard.chunk.outcome"
CHUNK_BACKLOG_MS = "blizzard.chunk.backlog_ms"
CHUNK_ACTIVE_MS = "blizzard.chunk.active_ms"
CHUNK_STEPS = "blizzard.chunk.steps"
CHUNK_BOUNCES = "blizzard.chunk.bounces"
CHUNK_INPUT_TOKENS = "blizzard.chunk.input_tokens"
CHUNK_OUTPUT_TOKENS = "blizzard.chunk.output_tokens"
CHUNK_CACHE_READ_TOKENS = "blizzard.chunk.cache_read_tokens"
CHUNK_CACHE_CREATE_TOKENS = "blizzard.chunk.cache_create_tokens"
CHUNK_COST_USD = "blizzard.chunk.cost.usd"
CHUNK_COST_ESTIMATED = "blizzard.chunk.cost.estimated"
CHUNK_COST_PARTIAL = "blizzard.chunk.cost.partial"

# Measures — the step root only
STEP_INPUT_TOKENS = "blizzard.step.input_tokens"
STEP_OUTPUT_TOKENS = "blizzard.step.output_tokens"
STEP_CACHE_READ_TOKENS = "blizzard.step.cache_read_tokens"
STEP_CACHE_CREATE_TOKENS = "blizzard.step.cache_create_tokens"
STEP_COST_USD = "blizzard.step.cost.usd"
STEP_COST_ESTIMATED = "blizzard.step.cost.estimated"
STEP_COST_PARTIAL = "blizzard.step.cost.partial"
WAIT_QUEUE_MS = "blizzard.step.wait.queue_ms"
WAIT_CLAIM_MS = "blizzard.step.wait.claim_ms"
WAIT_ASK_MS = "blizzard.step.wait.ask_ms"
WAIT_PAUSE_MS = "blizzard.step.wait.pause_ms"
WAIT_PICKUP_MS = "blizzard.step.wait.pickup_ms"

# Resource
SERVICE_VERSION = "service.version"

# Span and event names
EVENT_INVOCATION = "invocation"
EVENT_HUB_POLL = "hub poll pending"
EVENT_BOUNCE = "bounce"

_PREFIXES = ("blizzard.", "gen_ai.", "service.")

#: Every attribute key a step's trace declares — the set an emitted key must belong to.
DECLARED_ATTRIBUTES: frozenset[str] = shared.SHARED_ATTRIBUTES | frozenset(
    value
    for name, value in tuple(globals().items())
    if name.isupper()
    and isinstance(value, str)
    and value.startswith(_PREFIXES)
    and not name.endswith("INSTRUMENTATION_SCOPE")
)


def resource_attributes(environ: Mapping[str, str], version: str) -> dict[str, str]:
    """The hub's resource attributes. ``service.name`` is ``blizzard-hub`` only when neither
    ``OTEL_SERVICE_NAME`` nor a ``service.name`` entry of ``OTEL_RESOURCE_ATTRIBUTES`` names one."""
    name = shared.service_name(environ, DEFAULT_SERVICE_NAME)
    return {shared.SERVICE_NAME: name, SERVICE_VERSION: version, shared.TRACE_SCHEMA_VERSION: shared.SCHEMA_VERSION}
