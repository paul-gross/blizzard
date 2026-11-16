import { chunkSummary } from 'fleet/testing';
import type { UrgencyView } from 'fleet';
import { bannerUrgencyIcons } from '../../core/urgencies.model';
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
  it("names a pending urgency's banner icon over the chunk's settled urgency", () => {
    const normal = { urgency_id: 'n', slug: 'normal', default: true } as UrgencyView;
    const imaged = { urgency_id: 'i', slug: 'imaged', default: false, emoji: null, icon: { digest: 'd1', media_type: 'image/png' } } as UrgencyView;
    const chunk = chunkSummary({ chunk_id: 'c', graph_id: 'gr_1', status: 'ready', urgency: { urgency_id: 'n', slug: 'normal' } });
    const chunks = withPendingUrgencies([chunk], [{ chunkId: 'c', urgencyId: 'i' }], [normal, imaged]);
    expect(bannerUrgencyIcons([chunk], [normal, imaged])).toEqual([]);
    expect(bannerUrgencyIcons(chunks, [normal, imaged])).toEqual([{ key: 'i', digest: 'd1' }]);
  });
});
