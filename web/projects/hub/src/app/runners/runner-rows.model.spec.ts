import type { RunnerRegistryView } from 'fleet';
import type { ChunkSummary } from 'fleet/shell';

import { foldRunnerRows, lensRunners } from './runner-rows.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

function runner(id: string): RunnerRegistryView {
  return { runner_id: id, subscriptions: [] } as unknown as RunnerRegistryView;
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
    } as unknown as RunnerRegistryView;
    const [row] = foldRunnerRows([r], [], NOW);
    expect(row.nowMs).toBe(NOW);
    expect(row.subscriptionPaces[0].paceBars[0].elapsedPct).toBe(50);
    expect(row.subscriptionPaces[0].freshness).toBe('fresh');
    expect(row.subscriptionPaces[0].condition).toBeNull();
  });

  it('names each project the runner serves, and marks an unassigned runner and an unresolved entry', () => {
    const serving = {
      runner_id: 'r_serving',
      subscriptions: [],
      projects: [
        { entry: 'default', project_id: 'proj_d', slug: 'default', name: 'Default' },
        { entry: 'proj_g', project_id: 'proj_g', slug: 'glacier', name: null },
        { entry: 'celestial', project_id: null, slug: null, name: null },
      ],
    } as unknown as RunnerRegistryView;
    const unassigned = { runner_id: 'r_none', subscriptions: [], projects: [] } as unknown as RunnerRegistryView;

    const [servingRow, unassignedRow] = foldRunnerRows([serving, unassigned], [], NOW);

    expect(servingRow.served).toEqual(['Default', 'glacier']);
    expect(servingRow.unassigned).toBe(false);
    expect(servingRow.unresolved).toEqual(['celestial']);
    expect(unassignedRow.served).toEqual([]);
    expect(unassignedRow.unassigned).toBe(true);
    expect(unassignedRow.unresolved).toEqual([]);
  });
});

describe('lensRunners', () => {
  const declaring = (id: string, ...slugs: (string | null)[]) =>
    ({
      runner_id: id,
      projects: slugs.map((slug) => ({ entry: slug ?? 'gone', project_id: slug === null ? null : `proj_${slug}`, slug, name: null })),
    }) as unknown as RunnerRegistryView;

  const runners = [
    declaring('r_alpha', 'alpha'),
    declaring('r_both', 'beta', 'alpha'),
    declaring('r_beta', 'beta'),
    declaring('r_none'),
    declaring('r_unresolved', null),
  ];

  it('shows every runner on All', () => {
    expect(lensRunners(runners, null)).toBe(runners);
  });

  it('shows the runners serving the lensed project, and the unassigned ones, under a lens', () => {
    expect(lensRunners(runners, 'alpha').map((r) => r.runner_id)).toEqual(['r_alpha', 'r_both', 'r_none']);
  });
});
