import type { runnerApi } from 'fleet';

import type { RunnerDashboard } from '../../core/panel-reads.model';
import { hubSummary, latestFleetSummary } from './hub-panel.model';

type RunnerStatus = runnerApi.DashboardView['runner'];

function entry(
  runnerId: string,
  runner: Partial<Omit<RunnerStatus, 'hub'>> & { hub?: Partial<RunnerStatus['hub']> } = {},
  fleet: runnerApi.FleetSummaryView | null = null,
): RunnerDashboard {
  const { hub, ...rest } = runner;
  return {
    owner: { runnerId, name: runnerId.replace('rn_', 'r'), label: runnerId.replace('rn_', 'r') },
    dashboard: {
      fleet_summary: fleet,
      runner: {
        runner_id: runnerId,
        workspace_id: 'ws',
        pause: { local: false, hub: false, effective: false },
        capacities: { used: 0, bindings: [] },
        hub: { endpoint: 'http://hub.example', reachable: true, last_contact_at: null, buffer_depth: 0, ...hub },
        last_tick_at: null,
        ...rest,
      },
    } as runnerApi.DashboardView,
  };
}

const FLEET = (ready: number): runnerApi.FleetSummaryView => ({ ready, running: 2, waiting: 1, needs: 0 });

describe('hubSummary', () => {
  it('is null before any dashboard lands', () => {
    expect(hubSummary([])).toBeNull();
  });

  it("folds every runner's link into one reading off the first runner's hub", () => {
    const summary = hubSummary([
      entry('rn_1', { hub: { last_contact_at: '2026-07-16T11:00:00.000Z', buffer_depth: 2 } }),
      entry('rn_2', { hub: { endpoint: 'http://other.example', last_contact_at: '2026-07-16T11:05:00.000Z', buffer_depth: 3 } }),
    ]);

    expect(summary).toEqual({
      endpoint: 'http://hub.example',
      reachable: 2,
      total: 2,
      lastContactAt: '2026-07-16T11:05:00.000Z',
      buffered: 5,
      issues: [],
    });
  });

  it('has no contact before any runner made one', () => {
    expect(hubSummary([entry('rn_1')])?.lastContactAt).toBeNull();
  });

  it('counts the runners reaching the hub and names each one that cannot', () => {
    const summary = hubSummary([entry('rn_1'), entry('rn_2', { hub: { reachable: false } })]);

    expect(summary?.reachable).toBe(1);
    expect(summary?.total).toBe(2);
    expect(summary?.issues).toEqual([{ key: 'rn_2:link', runner: 'r2', text: 'cannot reach the hub', title: 'http://hub.example' }]);
  });

  it('words each identity problem for the operator, the remedy on hover', () => {
    const identity = (state: string, extra: Partial<runnerApi.IdentityVerdictView> = {}) =>
      ({ state, store_runner_id: null, token_runner_id: null, checked_at: null, reset_command: 'reset it', reason: null, ...extra }) as runnerApi.IdentityVerdictView;
    const summary = hubSummary([
      entry('rn_1', { identity: identity('mismatched', { token_runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3' }) }),
      entry('rn_2', { identity: identity('refused', { reason: 'token revoked' }) }),
      entry('rn_3', { identity: identity('not_held', { reason: 'held elsewhere' }) }),
      entry('rn_4', { identity: identity('tenant_differs') }),
      entry('rn_5', { identity: identity('matched') }),
    ]);

    expect(summary?.issues).toEqual([
      { key: 'rn_1:identity', runner: 'r1', text: 'refusing — its token belongs to R-ABF3', title: 'reset it' },
      { key: 'rn_2:identity', runner: 'r2', text: 'the hub refuses its token', title: 'token revoked — reset it' },
      { key: 'rn_3:identity', runner: 'r3', text: 'halted — not held by its host', title: 'held elsewhere' },
      { key: 'rn_4:identity', runner: 'r4', text: "halted — tenant differs from its host's", title: null },
    ]);
  });

  it('names each routing conflict, claiming nothing', () => {
    const summary = hubSummary([
      entry('rn_1', { routing_conflicts: [{ project_id: 'proj_1', entries: ['blizzard', 'bz'] }] }),
    ]);

    expect(summary?.issues).toEqual([
      { key: 'rn_1:conflict:proj_1', runner: 'r1', text: 'blizzard, bz name one project — claiming nothing', title: 'proj_1' },
    ]);
  });
});

describe('latestFleetSummary', () => {
  it('is null while no runner carries the counts', () => {
    expect(latestFleetSummary([])).toBeNull();
    expect(latestFleetSummary([entry('rn_1')])).toBeNull();
  });

  it('reads the first runner whose read carries them', () => {
    const fleet = FLEET(4);
    expect(latestFleetSummary([entry('rn_1'), entry('rn_2', {}, fleet), entry('rn_3', {}, FLEET(9))])).toBe(fleet);
  });
});
