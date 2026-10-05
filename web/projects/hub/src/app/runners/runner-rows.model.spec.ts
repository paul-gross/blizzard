import type { RunnerView } from 'fleet';
import type { ChunkSummary } from 'fleet/shell';

import { foldRunnerRows } from './runner-rows.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

function runner(id: string): RunnerView {
  return { runner_id: id, subscriptions: [] } as unknown as RunnerView;
}

function chunk(id: string, runnerId: string | null, envs: number): ChunkSummary {
  return {
    chunk_id: id,
    runner_id: runnerId,
    current_node_name: 'build',
    status: 'in_progress',
    environment_count: envs,
  } as unknown as ChunkSummary;
}

describe('foldRunnerRows', () => {
  it('folds each runner\'s claims and summed environment count, ignoring unrouted chunks', () => {
    const rows = foldRunnerRows(
      [runner('r1'), runner('r2')],
      [chunk('ch_a', 'r1', 1), chunk('ch_b', 'r1', 3), chunk('ch_c', null, 2)],
      NOW,
    );
    expect(rows[0].claims.map((c) => c.chunkId)).toEqual(['ch_a', 'ch_b']);
    expect(rows[0].used).toBe(4);
    expect(rows[1].claims).toEqual([]);
    expect(rows[1].used).toBe(0);
  });

  it('stamps every row with the one clock reading and derives subscription pace against it', () => {
    const r = {
      runner_id: 'r1',
      subscriptions: [
        {
          slug: 's',
          name: 'S',
          windows: [{ window: '5h', utilization_pct: 40, resets_at: '2026-07-16T14:30:00.000Z', window_seconds: 18000 }],
          sampled_at: '2026-07-16T11:50:00.000Z',
        },
      ],
    } as unknown as RunnerView;
    const [row] = foldRunnerRows([r], [], NOW);
    expect(row.nowMs).toBe(NOW);
    expect(row.subscriptionPaces[0].paceBars[0].elapsedPct).toBe(50);
    expect(row.subscriptionPaces[0].freshness).toBe('fresh');
    expect(row.subscriptionPaces[0].condition).toBeNull();
  });
});
