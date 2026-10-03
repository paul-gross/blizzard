"""The trace attribute names both daemons emit, and the GenAI usage mapping they share — the single home.

Daemon-only names, instrumentation scopes and service defaults stay in each daemon's own attribute module.
Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md`` §Attributes and §GenAI usage."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.trace_spans import AttributeValue

SCHEMA_VERSION = "1"

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
HARNESS_ID = "blizzard.harness.id"
HARNESS_VERSION = "blizzard.harness.version"

# Invocation
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
TRACE_SCHEMA_VERSION = "blizzard.trace.schema_version"

_PREFIXES = ("blizzard.", "gen_ai.", "service.")

#: Every attribute key this module declares.
SHARED_ATTRIBUTES: frozenset[str] = frozenset(
    value
    for name, value in tuple(globals().items())
    if name.isupper() and isinstance(value, str) and value.startswith(_PREFIXES)
)


def genai_usage(
    *, input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_create_tokens: int
) -> dict[str, AttributeValue]:
    """One invocation's token counts as ``gen_ai.usage.*`` and ``blizzard.invocation.*`` attributes.

    GenAI input tokens include both cache counts; the blizzard names keep each count on its own."""
    return {
        GEN_AI_INPUT_TOKENS: input_tokens + cache_read_tokens + cache_create_tokens,
        GEN_AI_OUTPUT_TOKENS: output_tokens,
        GEN_AI_CACHE_READ_TOKENS: cache_read_tokens,
        GEN_AI_CACHE_CREATE_TOKENS: cache_create_tokens,
        INVOCATION_INPUT_TOKENS: input_tokens,
        INVOCATION_OUTPUT_TOKENS: output_tokens,
        INVOCATION_CACHE_READ_TOKENS: cache_read_tokens,
        INVOCATION_CACHE_CREATE_TOKENS: cache_create_tokens,
    }


def service_name(environ: Mapping[str, str], default: str) -> str:
    return environ.get("OTEL_SERVICE_NAME") or _resource_entries(environ).get(SERVICE_NAME) or default


def _resource_entries(environ: Mapping[str, str]) -> dict[str, str]:
    entries: dict[str, str] = {}
    for pair in environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        key, sep, value = pair.partition("=")
        if sep and key.strip() and value.strip():
            entries[key.strip()] = value.strip()
    return entries
