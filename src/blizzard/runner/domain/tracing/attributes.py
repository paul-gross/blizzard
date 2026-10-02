"""The runner's own trace attribute names, event and span names, and instrumentation scope; names the hub also
emits live in ``blizzard.foundation.trace_attributes``.

Contract: ``blizzard-product:/plans/tracing/runner-spans/spec/spans.md`` §Attributes, §Span events, §GenAI conventions.
"""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation import trace_attributes as shared

INSTRUMENTATION_SCOPE = "blizzard.runner.runner_spans"
INSTRUMENTATION_SCOPE_VERSION = "1"
DEFAULT_SERVICE_NAME = "blizzard-runner"

# Dimensions
RUNNER_ID = "blizzard.runner.id"

# Lease and session
LEASE_ID = "blizzard.lease.id"
LEASE_CLOSE_REASON = "blizzard.lease.close_reason"
SESSION_NAME = "blizzard.session.name"
MODEL_RESOLVED = "blizzard.model.resolved"
EFFORT_RESOLVED = "blizzard.effort.resolved"

# Invocation
INVOCATION_NUDGE = "blizzard.invocation.nudge"
INVOCATION_GENERATION = "blizzard.invocation.generation"
INVOCATION_END_SOURCE = "blizzard.invocation.end_source"
OVERLOAD_STREAK = "blizzard.overload.streak"

# Event attributes
CONTEXT_TOKENS = "blizzard.context.tokens"
CHECK_INDEX = "blizzard.check.index"
CHECK_PASSED = "blizzard.check.passed"
CHECKS_PASSED = "blizzard.checks.passed"
CHECKS_COUNT = "blizzard.checks.count"

# GenAI semantic conventions (``shared.GENAI_SEMCONV_VERSION``)
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
INVOKE_AGENT = "invoke_agent"

# Resource
SERVICE_VERSION = "service.version"

# Event names
EVENT_SESSION_IDENTIFIED = "session identified"
EVENT_SESSION_END = "session end"
EVENT_CONTEXT_SAMPLE = "context sample"
EVENT_NUDGE = "nudge"
EVENT_CHECK = "check"
EVENT_CHECKS_RAN = "checks ran"

_PREFIXES = ("blizzard.", "gen_ai.")

#: Every attribute key a lease's spans declare — the set an emitted key must belong to.
DECLARED_ATTRIBUTES: frozenset[str] = shared.SHARED_ATTRIBUTES | frozenset(
    value
    for name, value in tuple(globals().items())
    if name.isupper() and isinstance(value, str) and value.startswith(_PREFIXES) and name != "INSTRUMENTATION_SCOPE"
)


def resource_attributes(environ: Mapping[str, str], version: str) -> dict[str, str]:
    """The runner's resource attributes. ``service.name`` is ``blizzard-runner`` only when neither
    ``OTEL_SERVICE_NAME`` nor a ``service.name`` entry of ``OTEL_RESOURCE_ATTRIBUTES`` names one."""
    name = shared.service_name(environ, DEFAULT_SERVICE_NAME)
    return {shared.SERVICE_NAME: name, SERVICE_VERSION: version, shared.TRACE_SCHEMA_VERSION: shared.SCHEMA_VERSION}
