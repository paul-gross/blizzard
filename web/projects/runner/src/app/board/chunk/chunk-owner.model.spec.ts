import type { PanelLease, RunnerOwner } from '../../core/panel-reads.model';
import { chunkReadOwner } from './chunk-owner.model';

const R1: RunnerOwner = { runnerId: 'rn_1', name: 'r1', label: 'r1' };
const R2: RunnerOwner = { runnerId: 'rn_2', name: 'r2', label: 'r2' };

function lease(chunkId: string, owner: RunnerOwner): PanelLease {
  return { chunk_id: chunkId, owner, projectSlug: null } as unknown as PanelLease;
}

describe('chunkReadOwner', () => {
  it('owns a leased chunk by its lease runner', () => {
    expect(chunkReadOwner([lease('ch_a', R2)], 'ch_a', [R1, R2], [R1, R2])).toBe(R2);
  });

  it('owns a chunk that moved runners by its freshest lease runner', () => {
    expect(chunkReadOwner([lease('ch_a', R2), lease('ch_a', R1)], 'ch_a', [R1, R2], [R1, R2])).toBe(R2);
  });

  it('falls back to the in-scope serving runner for a chunk no lease names', () => {
    expect(chunkReadOwner([lease('ch_b', R1)], 'ch_a', [R1, R2], [R2])).toBe(R2);
  });

  it('falls back to the first serving runner when the scoped runner is not serving', () => {
    expect(chunkReadOwner([], 'ch_a', [R1, R2], [])).toBe(R1);
  });

  it('owns nothing while no runner is serving', () => {
    expect(chunkReadOwner([], 'ch_a', [], [])).toBeNull();
  });
});
