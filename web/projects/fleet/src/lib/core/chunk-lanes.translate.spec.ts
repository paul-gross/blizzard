import { ChunkStatus, type ChunkCountsView } from '../api/hub';
import { laneCountInput, laneStatus } from './chunk-lanes.translate';
import { chunkLane, laneCounts, unknownLane } from './chunk-lanes';

describe('chunk lane wire translation', () => {
  it('maps every declared status and preserves unknown raw spelling', () => {
    for (const status of Object.values(ChunkStatus)) expect(laneStatus(status)).toEqual({ kind: status });
    expect(laneStatus('Future Status')).toEqual({ kind: 'unknown', raw: 'Future Status' });
    expect(unknownLane([{ status: laneStatus('Future Status') }])?.label).toBe('Future Status');
    expect(chunkLane({ status: laneStatus('Future Status'), terminal: true })).toBe('done');
  });

  it('retains backend total and terminal counts', () => {
    const counts: ChunkCountsView = { not_ready: 1, ready: 2, running: 3, delivering: 4, waiting_on_human: 5, needs_human: 6, paused: 7, stopped: 8, done: 9, total: 99, terminal: 20, done_since: null };
    const input = laneCountInput(counts);
    expect(input.total).toBe(99);
    expect(Object.fromEntries(laneCounts(input))).toEqual({ notready: 1, ready: 2, waiting: 12, needs: 6, running: 7, done: 20 });
  });
});
