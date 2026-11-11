import type { hubApi } from 'fleet';
import { nextClaimSummary, prerequisiteIds, prerequisiteStatuses } from './runner-next-claim.model';

describe('nextClaimSummary', () => {
  it('preserves the hub walk and reports omitted passed entries', () => {
    const passed = [{ chunk_id: 'ch_1', reason: 'blocked' }] as hubApi.NextClaimPassedEntry[];
    const response: hubApi.NextClaimResponse = {
      next: null, passed, passed_total: 23, unserved: 2, if_holding: null, held_at: passed,
    };
    expect(nextClaimSummary(response)).toEqual({
      next: null, passed, omitted: 22, unserved: 2, ifHolding: null, heldAt: passed,
    });
  });
});

describe('prerequisite display reads', () => {
  it('deduplicates prerequisites across passed and held heads', () => {
    const passed = [{ prerequisite: 'ch_a' }, { prerequisite: null }, { prerequisite: 'ch_b' }] as hubApi.NextClaimPassedEntry[];
    expect(prerequisiteIds({ next: null, passed, passed_total: 3, held_at: [passed[0]], if_holding: null, unserved: 0 })).toEqual(['ch_a', 'ch_b']);
    expect(prerequisiteIds(undefined)).toEqual([]);
  });
  it('only uses returned tenant chunk statuses', () => {
    expect(prerequisiteStatuses([{ data: { chunk_id: 'ch_a', status: 'running' } as hubApi.ChunkDetail }, { data: { chunk_id: 'ch_b', status: 'ready' } as hubApi.ChunkDetail, isError: true }, {}])).toEqual({ ch_a: 'running' });
  });
});
