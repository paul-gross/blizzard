import { type ActivityView } from 'fleet';
import { translateActivityRow } from './activity.query';

describe('translateActivityRow', () => {
  it('converges a durable row on the live activity fact shape', () => {
    const row = { at: '2026-06-01T12:00:00Z', type: 'chunk-changed', key: 'k1', chunk_id: 'ch_1', cause: 'completed' } as ActivityView;
    expect(translateActivityRow(row)).toEqual({
      at: Date.parse(row.at), type: row.type, key: 'k1', data: { key: 'k1', chunk_id: 'ch_1', cause: 'completed' },
    });
  });
});
