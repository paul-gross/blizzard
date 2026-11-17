import type { hubApi } from 'fleet';
import { claimUrgencyIcons, nextClaimSummary, prerequisiteIds, prerequisiteStatuses } from './runner-next-claim.model';

describe('nextClaimSummary', () => {
  it('preserves the hub walk and reports omitted passed entries', () => {
    const passed = [{ chunk_id: 'ch_1', reason: 'blocked' }] as hubApi.NextClaimPassedEntry[];
    const response: hubApi.NextClaimResponse = {
      next: null, passed, passed_total: 23, unserved: 2, if_holding: null, held_at: passed, without_repository_targets: null,
    };
    expect(nextClaimSummary(response)).toEqual({
      next: null, passed, omitted: 22, unserved: 2, ifHolding: null, heldAt: passed,
    });
  });
});

describe('prerequisite display reads', () => {
  it('deduplicates prerequisites across passed and held heads', () => {
    const passed = [{ prerequisite: 'ch_a' }, { prerequisite: null }, { prerequisite: 'ch_b' }] as hubApi.NextClaimPassedEntry[];
    expect(prerequisiteIds({ next: null, passed, passed_total: 3, held_at: [passed[0]], if_holding: null, unserved: 0, without_repository_targets: null })).toEqual(['ch_a', 'ch_b']);
    expect(prerequisiteIds(undefined)).toEqual([]);
  });
  it('only uses returned tenant chunk statuses', () => {
    expect(prerequisiteStatuses([{ data: { chunk_id: 'ch_a', status: 'running' } as hubApi.ChunkDetail }, { data: { chunk_id: 'ch_b', status: 'ready' } as hubApi.ChunkDetail, isError: true }, {}])).toEqual({ ch_a: 'running' });
  });
});

describe('claimUrgencyIcons', () => {
  it('collects icon-backed non-default urgencies across every part of the walk once', () => {
    const ref = (urgency_id: string) => ({ urgency: { urgency_id, slug: urgency_id } });
    const data = { next: ref('a'), passed: [ref('b')], held_at: [ref('a')], if_holding: ref('c'), without_repository_targets: null } as hubApi.NextClaimResponse;
    const values = [
      { urgency_id: 'a', icon: { digest: 'da' }, default: false, emoji: null },
      { urgency_id: 'b', icon: { digest: 'db' }, default: false, emoji: null },
      { urgency_id: 'c', icon: { digest: 'dc' }, default: true, emoji: null },
      { urgency_id: 'unused', icon: { digest: 'du' }, default: false, emoji: null },
    ] as hubApi.UrgencyView[];
    expect(claimUrgencyIcons(data, values)).toEqual([{ key: 'a', digest: 'da' }, { key: 'b', digest: 'db' }]);
  });
});

describe('the no-target alternative', () => {
  const ref = (urgency_id: string) => ({ urgency: { urgency_id, slug: urgency_id } });
  const alternative = { next: ref('z'), passed: [{ ...ref('y'), prerequisite: 'ch_c' }], held_at: [], if_holding: null, excluded: 1 };
  const response = { next: null, passed: [], held_at: [], if_holding: null, without_repository_targets: alternative } as unknown as hubApi.NextClaimResponse;
  it('resolves prerequisites and urgency icons for its entries too', () => {
    expect(prerequisiteIds(response)).toEqual(['ch_c']);
    const values = [{ urgency_id: 'z', icon: { digest: 'dz' }, default: false, emoji: null }, { urgency_id: 'y', icon: { digest: 'dy' }, default: false, emoji: null }] as hubApi.UrgencyView[];
    expect(claimUrgencyIcons(response, values)).toEqual([{ key: 'z', digest: 'dz' }, { key: 'y', digest: 'dy' }]);
  });
  it('is skipped when null or absent', () => {
    expect(prerequisiteIds({ ...response, without_repository_targets: null })).toEqual([]);
  });
});
