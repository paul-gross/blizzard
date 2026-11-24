import { describe, expect, it } from 'vitest';
import { runnerFactView } from 'fleet/testing';

import { fromFactView } from './fact.translate';

describe('fromFactView', () => {
  it('carries the fact, its attribution and its ack', () => {
    const wire = runnerFactView({ seq: 9, kind: 'lease.closed', chunk_id: 'ch_1', lease_id: 'L-1', acked_at: 't' });
    expect(fromFactView(wire)).toEqual({
      seq: 9,
      kind: 'lease.closed',
      chunk_id: 'ch_1',
      project_id: null,
      lease_id: 'L-1',
      created_at: wire.created_at,
      acked_at: 't',
    });
  });
});
