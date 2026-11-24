import type { HubRunner, RunnerDeclining } from '../core/runners.domain';
import { hubRunner } from '../core/runners.builders';
import { runnerClaimChunk } from './runner-claims.builders';
import type { RunnerClaimChunk } from './runner-claims.domain';

import { decliningLabel, foldRunnerRows, lensRunners, versionStandingBadge } from './runner-rows.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

function runner(id: string): HubRunner {
  return hubRunner({ runner_id: id });
}

function chunk(id: string, runnerId: string | null, envs: number): RunnerClaimChunk {
  return runnerClaimChunk({
    chunk_id: id,
    runner_id: runnerId,
    current_node_name: 'build',
    laneStatus: { kind: 'unknown', raw: 'in_progress' },
    environment_count: envs,
  });
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
    const r = hubRunner({
      runner_id: 'r1',
      subscriptions: [
        {
          slug: 's',
          name: 'S',
          windows: [{ window: '5h', utilization_pct: 40, resets_at: '2026-07-16T14:30:00.000Z', window_seconds: 18000 }],
          sampled_at: '2026-07-16T11:50:00.000Z',
          condition: null,
          miss_reason: null,
        },
      ],
    });
    const [row] = foldRunnerRows([r], [], NOW);
    expect(row.nowMs).toBe(NOW);
    expect(row.subscriptionPaces[0].paceBars[0].elapsedPct).toBe(50);
    expect(row.subscriptionPaces[0].freshness).toBe('fresh');
    expect(row.subscriptionPaces[0].condition).toBeNull();
  });

  it('names each project the runner serves, and marks an unassigned runner and an unresolved entry', () => {
    const served = { claimable: true, color: null };
    const serving = hubRunner({
      runner_id: 'r_serving',
      projects: [
        { ...served, entry: 'default', project_id: 'proj_d', slug: 'default', name: 'Default' },
        { ...served, entry: 'proj_g', project_id: 'proj_g', slug: 'glacier', name: null },
        { ...served, entry: 'celestial', project_id: null, slug: null, name: null },
      ],
    });
    const unassigned = hubRunner({ runner_id: 'r_none' });

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
    hubRunner({
      runner_id: id,
      projects: slugs.map((slug) => ({ entry: slug ?? 'gone', project_id: slug === null ? null : `proj_${slug}`, slug, name: null, claimable: true, color: null })),
    });

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

describe('versionStandingBadge', () => {
  const judged = (over: Partial<HubRunner>): HubRunner => ({ ...runner('rn_a'), ...over });

  it('words each standing the hub judges, naming the reason of a refusal', () => {
    expect(versionStandingBadge(judged({ version_standing: 'in_window' }))).toEqual({ label: 'in window', tone: 'running' });
    expect(versionStandingBadge(judged({ version_standing: 'unversioned' }))).toEqual({ label: 'unversioned', tone: 'idle' });
    expect(versionStandingBadge(judged({ version_standing: 'refused', version_refusal: 'too-old' }))).toEqual({
      label: 'refused (too-old)',
      tone: 'needs',
    });
    expect(versionStandingBadge(judged({ version_standing: 'refused', version_refusal: 'too-new' }))?.label).toBe('refused (too-new)');
    expect(versionStandingBadge(judged({ version_standing: 'refused', version_refusal: 'unversioned' }))?.label).toBe(
      'refused (unversioned)',
    );
  });

  it('words a standing the client does not declare raw, in idle', () => {
    expect(versionStandingBadge(judged({ version_standing: { kind: 'unknown', raw: 'quarantined' } }))).toEqual({ label: 'quarantined', tone: 'idle' });
  });

  it('shows no badge for a runner the hub has not judged', () => {
    expect(versionStandingBadge(judged({ version_standing: null }))).toBeNull();
    expect(versionStandingBadge(judged({}))).toBeNull();
  });
});

describe('decliningLabel', () => {
  const reason = (lever_kind: RunnerDeclining['lever_kind'], extra: Partial<RunnerDeclining> = {}): RunnerDeclining =>
    ({ lever: typeof lever_kind === 'string' ? lever_kind : lever_kind.raw, lever_kind, detail: 'at 95%', until: null, subscription: null, ...extra });

  it('words the floor from the subscription it waits on and when it resets', () => {
    const label = decliningLabel(reason('floor', { subscription: 'max', until: '2026-07-16T13:00:00.000Z' }), NOW);
    expect(label).toMatch(/^waiting on max — at 95%, resets /);
  });

  it('words a known non-floor lever through the generic branch', () => {
    expect(decliningLabel(reason('pace'), NOW)).toBe('declining — pace: at 95%');
  });

  it('words a lever outside the generated vocabulary from lever_kind verbatim', () => {
    expect(decliningLabel(reason({ kind: 'unknown', raw: 'some_future_lever' }, { lever: 'stale' }), NOW)).toBe('declining — some_future_lever: at 95%');
  });
});
