import type { runnerApi } from 'fleet/runner-shell';

import type { Escalation } from './escalation.domain';

export function fromEscalationView(wire: runnerApi.EscalationView): Escalation {
  return {
    lease_id: wire.lease_id,
    chunk_id: wire.chunk_id,
    project_id: wire.project_id ?? null,
    node_id: wire.node_id,
    epoch: wire.epoch,
    cause: wire.cause ?? null,
    closed_at: wire.closed_at,
    closure: wire.closure ? { closed_at: wire.closure.closed_at, reason: wire.closure.reason } : null,
    harness_id: wire.harness_id ?? null,
    harness_version: wire.harness_version ?? null,
    model: wire.model ?? null,
    effort: wire.effort ?? null,
    session_name: wire.session_name ?? null,
    resume_command: wire.resume_command,
    wrapped_takeover_command: wire.wrapped_takeover_command ?? null,
  };
}
