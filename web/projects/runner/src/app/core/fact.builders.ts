import type { Fact } from './fact.domain';

/** One ledger fact, unattributed and unacked. */
export function fact(overrides: Partial<Fact> = {}): Fact {
  return {
    seq: 1,
    kind: 'note',
    chunk_id: null,
    project_id: null,
    lease_id: null,
    created_at: '2026-01-01T00:00:00Z',
    acked_at: null,
    ...overrides,
  };
}
