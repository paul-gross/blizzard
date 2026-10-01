"""Every attribute name, scope and schema constant a step's trace emits — the single home.

Contract: ``blizzard-product:/plans/tracing/fleet-spans/spec/spans.md`` §Attributes and §GenAI usage."""

from __future__ import annotations

from collections.abc import Mapping

INSTRUMENTATION_SCOPE = "blizzard.hub.fleet_spans"
INSTRUMENTATION_SCOPE_VERSION = "1"
SCHEMA_VERSION = "1"
DEFAULT_SERVICE_NAME = "blizzard-hub"

#: The OpenTelemetry semantic-conventions release the GenAI names below are pinned to.
GENAI_SEMCONV_VERSION = "1.44.0"

# Dimensions
CHUNK_ID = "blizzard.chunk.id"
CHUNK_WORK_REFS = "blizzard.chunk.work_refs"
GRAPH_NAME = "blizzard.graph.name"
GRAPH_ID = "blizzard.graph.id"
NODE_NAME = "blizzard.node.name"
NODE_ID = "blizzard.node.id"
NODE_EXECUTOR = "blizzard.node.executor"
STEP_EPOCH = "blizzard.step.epoch"
STEP_VISIT = "blizzard.step.visit"
STEP_OUTCOME = "blizzard.step.outcome"
STEP_CHOICE = "blizzard.step.choice"
STEP_TO_NODE_NAME = "blizzard.step.to_node.name"
STEP_PRECEDED_BY = "blizzard.step.preceded_by"
RUNNER_ID = "blizzard.runner.id"
HARNESS_ID = "blizzard.harness.id"
HARNESS_VERSION = "blizzard.harness.version"
STEP_MODELS = "blizzard.step.models"
BOUNCE_CAUSE = "blizzard.bounce.cause"
ASK_ANSWERED = "blizzard.ask.answered"
CLOCK_SKEW = "blizzard.clock_skew"
LINK_REASON = "blizzard.link.reason"

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

# Invocation event
INVOCATION_KIND = "blizzard.invocation.kind"
INVOCATION_INPUT_TOKENS = "blizzard.invocation.input_tokens"
INVOCATION_OUTPUT_TOKENS = "blizzard.invocation.output_tokens"
INVOCATION_CACHE_READ_TOKENS = "blizzard.invocation.cache_read_tokens"
INVOCATION_CACHE_CREATE_TOKENS = "blizzard.invocation.cache_create_tokens"
INVOCATION_COST_USD = "blizzard.invocation.cost.usd"
INVOCATION_COST_ESTIMATED = "blizzard.invocation.cost.estimated"

# GenAI semantic conventions (``GENAI_SEMCONV_VERSION``)
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_CACHE_READ_TOKENS = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_CACHE_CREATE_TOKENS = "gen_ai.usage.cache_creation.input_tokens"

# Resource
SERVICE_NAME = "service.name"
SERVICE_VERSION = "service.version"
TRACE_SCHEMA_VERSION = "blizzard.trace.schema_version"

# Span and event names
EVENT_INVOCATION = "invocation"
EVENT_HUB_POLL = "hub poll pending"
EVENT_BOUNCE = "bounce"

_PREFIXES = ("blizzard.", "gen_ai.", "service.")

#: Every attribute key this module declares — the set an emitted key must belong to.
DECLARED_ATTRIBUTES: frozenset[str] = frozenset(
    value
    for name, value in tuple(globals().items())
    if name.isupper() and isinstance(value, str) and value.startswith(_PREFIXES) and name != "INSTRUMENTATION_SCOPE"
)


def resource_attributes(environ: Mapping[str, str], version: str) -> dict[str, str]:
    """The hub's resource attributes. ``service.name`` is ``blizzard-hub`` only when neither
    ``OTEL_SERVICE_NAME`` nor a ``service.name`` entry of ``OTEL_RESOURCE_ATTRIBUTES`` names one."""
    name = environ.get("OTEL_SERVICE_NAME") or _resource_entries(environ).get(SERVICE_NAME) or DEFAULT_SERVICE_NAME
    return {SERVICE_NAME: name, SERVICE_VERSION: version, TRACE_SCHEMA_VERSION: SCHEMA_VERSION}


def _resource_entries(environ: Mapping[str, str]) -> dict[str, str]:
    entries: dict[str, str] = {}
    for pair in environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        key, sep, value = pair.partition("=")
        if sep and key.strip() and value.strip():
            entries[key.strip()] = value.strip()
    return entries
