"""The runner-minted fact kinds — ``noun.verb`` names, one definition shared by the runner that
mints a fact and the hub that ingests it."""

from __future__ import annotations

# Fact kinds the batched /events push accepts (``noun.verb`` names).
LEASE_MINTED = "lease.minted"
ESCALATION_RECORDED = "escalation.recorded"
# question.asked opens the ask; answer.delivered records that the resume ran.
QUESTION_ASKED = "question.asked"
ANSWER_DELIVERED = "answer.delivered"
# The runner's *own* brake, reported upward — a distinct concept from the
# hub's own pause, not a second spelling of it. Runner-scoped: no chunk_id, no lease_id.
RUNNER_LOCALLY_PAUSED = "runner.locally_paused"
RUNNER_LOCALLY_RESUMED = "runner.locally_resumed"
# One harness invocation's usage/cost telemetry — a fact, never a stored
# aggregate. Payload: {chunk_id, node_id, epoch, kind, model, tokens…, cost_usd|null,
# estimated_cost_usd|null}, the estimate absent from a runner predating it and kept apart
# from cost_usd — a runner-side estimate for a subscription invocation, never billed spend.
USAGE_RECORDED = "usage.recorded"
# One operationally-significant failure. Payload: {severity, kind,
# chunk_id|null, lease_id|null, node_name|null, message, detail|null}. Never token-gated.
EVENT_RECORDED = "event.recorded"
# An advisory sample of subscription rate-limit utilization, never one a
# status derives from. Payload: {slug, sampled_at, windows: [...], name|null}; upserted
# per (runner_id, slug), not appended.
EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED = "external_subscription_usage.sampled"
# A sampler miss, upserted per (runner_id, slug) beside the sample. Payload: {slug, name, missed_at, reason} only.
EXTERNAL_SUBSCRIPTION_USAGE_MISSED = "external_subscription_usage.missed"
