import type { Ask } from './ask.domain';

/** An open ask on lease `L-1`. */
export function ask(overrides: Partial<Ask> = {}): Ask {
  return {
    question_id: 'q-1',
    chunk_id: 'ch_1',
    project_id: null,
    lease_id: 'L-1',
    session_id: null,
    harness_id: null,
    question: '?',
    options: [],
    asked_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}
