import type { Escalation } from './escalation.domain';

/** An open escalation of lease `L-1` on `ch_1`, with no closure recorded. */
export function escalation(overrides: Partial<Escalation> = {}): Escalation {
  return {
    lease_id: 'L-1',
    chunk_id: 'ch_1',
    project_id: null,
    node_id: 'build',
    epoch: 1,
    cause: null,
    closed_at: '2026-01-01T00:00:00Z',
    closure: null,
    harness_id: null,
    harness_version: null,
    model: null,
    effort: null,
    session_name: null,
    resume_command: 'claude --resume s-1',
    wrapped_takeover_command: null,
    ...overrides,
  };
}
