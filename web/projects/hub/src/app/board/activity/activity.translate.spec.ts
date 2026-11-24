import { type ActivityView } from 'fleet';
import { translateActivityRow } from './activity.translate';

describe('translateActivityRow', () => {
  it('converges a durable row on the live activity fact shape', () => {
    const row = { at: '2026-06-01T12:00:00Z', type: 'chunk-changed', key: 'k1', chunk_id: 'ch_1', cause: 'completed' } as ActivityView;
    expect(translateActivityRow(row)).toEqual({
      at: Date.parse(row.at), type: row.type, key: 'k1', data: { key: 'k1', chunk_id: 'ch_1', cause: 'completed' },
    });
  });

  it('preserves an unforeseen row and its raw event type', () => {
    const row = { at: '2026-06-01T12:00:00Z', type: 'future', key: 'k2', future: 42 } as unknown as ActivityView;
    expect(translateActivityRow(row)).toEqual({
      at: Date.parse(row.at), type: { kind: 'unknown', raw: 'future' }, key: 'k2',
      data: { key: 'k2', future: 42, raw: row },
    });
  });
});
