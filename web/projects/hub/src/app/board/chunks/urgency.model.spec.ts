import { chunkSummary } from 'fleet/testing';
import type { UrgencyView } from 'fleet';
import { withPendingUrgencies } from './urgency.model';

describe('withPendingUrgencies', () => {
  it('overlays only matching chunks, with the latest pending value, without reordering', () => {
    const chunks = [chunkSummary({ chunk_id: 'a', graph_id: 'gr_1', status: 'ready' }), chunkSummary({ chunk_id: 'b', graph_id: 'gr_1', status: 'ready' })];
    const values = [{ urgency_id: 'u', slug: 'urgent' }, { urgency_id: 'v', slug: 'very' }] as UrgencyView[];
    const result = withPendingUrgencies(chunks, [{ chunkId: 'a', urgencyId: 'u' }, { chunkId: 'a', urgencyId: 'v' }], values);
    expect(result.map((c) => c.chunk_id)).toEqual(['a', 'b']);
    expect(result[0].urgency).toEqual({ urgency_id: 'v', slug: 'very' });
    expect(result[1]).toBe(chunks[1]);
  });
});
