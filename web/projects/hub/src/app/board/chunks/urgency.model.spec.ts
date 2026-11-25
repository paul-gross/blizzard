import { boardChunk } from '../board.fixture';
import type { Urgency } from 'fleet';
import { bannerUrgencyIcons } from '../../core/urgencies.model';
import { withPendingUrgencies } from './urgency.model';

describe('withPendingUrgencies', () => {
  it('overlays only matching chunks, with the latest pending value, without reordering', () => {
    const chunks = [boardChunk({ chunk_id: 'a', graph_id: 'gr_1', status: 'ready', laneStatus: { kind: 'ready' } }), boardChunk({ chunk_id: 'b', graph_id: 'gr_1', status: 'ready', laneStatus: { kind: 'ready' } })];
    const values = [{ id: 'u', slug: 'urgent' }, { id: 'v', slug: 'very' }] as Urgency[];
    const result = withPendingUrgencies(chunks, [{ chunkId: 'a', urgencyId: 'u' }, { chunkId: 'a', urgencyId: 'v' }], values);
    expect(result.map((c) => c.chunk_id)).toEqual(['a', 'b']);
    expect(result[0].urgency).toEqual({ id: 'v', slug: 'very' });
    expect(result[1]).toBe(chunks[1]);
  });
  it("names a pending urgency's banner icon over the chunk's settled urgency", () => {
    const normal = { id: 'n', slug: 'normal', default: true } as Urgency;
    const imaged = { id: 'i', slug: 'imaged', default: false, emoji: null, icon: { digest: 'd1', mediaType: 'image/png' } } as Urgency;
    const chunk = boardChunk({ chunk_id: 'c', graph_id: 'gr_1', status: 'ready', laneStatus: { kind: 'ready' }, urgency: { id: 'n', slug: 'normal' } });
    const chunks = withPendingUrgencies([chunk], [{ chunkId: 'c', urgencyId: 'i' }], [normal, imaged]);
    expect(bannerUrgencyIcons([{ terminal: chunk.terminal, urgency: { id: 'n', slug: 'normal' } }], [normal, imaged])).toEqual([]);
    expect(bannerUrgencyIcons(chunks, [normal, imaged])).toEqual([{ key: 'i', digest: 'd1' }]);
  });
});
