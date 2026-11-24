import { describe, expect, it } from 'vitest';
import { hubConnectivityView, pauseStateView, runnerDashboardView, runnerStatusView } from 'fleet/testing';

import { fromDashboardView, fromIdentityVerdictView, fromRunnerStatusView, fromUnenforceableGateView } from './runner-status.translate';

const verdict = { state: 'matched', reason: null, reset_command: 'reset', token_runner_id: null, store_runner_id: null, checked_at: null } as const;

describe('fromIdentityVerdictView', () => {
  it('carries a known state and the refusal facts', () => {
    expect(fromIdentityVerdictView({ ...verdict, state: 'mismatched', token_runner_id: 'rn_2' })).toEqual({
      ...verdict,
      state: 'mismatched',
      token_runner_id: 'rn_2',
    });
  });

  it('maps a state this build cannot name onto unknown, raw', () => {
    expect(fromIdentityVerdictView({ ...verdict, state: 'quarantined' as never })?.state).toEqual({ kind: 'unknown', raw: 'quarantined' });
  });

  it('reads an absent verdict as null', () => {
    expect(fromIdentityVerdictView(null)).toBeNull();
  });
});

describe('fromUnenforceableGateView', () => {
  it('carries a known reason and maps an unforeseen one onto unknown', () => {
    expect(fromUnenforceableGateView({ node: 'deliver', reason: 'hub-executed' })).toEqual({ node: 'deliver', reason: 'hub-executed' });
    expect(fromUnenforceableGateView({ node: 'deliver', reason: 'retired' as never }).reason).toEqual({ kind: 'unknown', raw: 'retired' });
  });
});

describe('fromRunnerStatusView', () => {
  it('carries hub reachability and the effective brake from their fields', () => {
    const status = fromRunnerStatusView(
      runnerStatusView({
        hub: hubConnectivityView({ reachable: false, buffer_depth: 3 }),
        pause: pauseStateView({ local: false, hub: false, effective: true }),
      }),
    );
    expect(status?.hub.reachable).toBe(false);
    expect(status?.hub.buffer_depth).toBe(3);
    expect(status?.pause).toEqual({ local: false, hub: false, effective: true, local_reason: null });
  });

  it('reads absent unenforceable gates as none', () => {
    expect(fromRunnerStatusView(runnerStatusView({ unenforceable_gates: null }))?.unenforceable_gates).toEqual([]);
  });

  it('keeps served projects absent while the hub has resolved none', () => {
    expect(fromRunnerStatusView(runnerStatusView({ served_projects: null }))?.served_projects).toBeNull();
  });

  it('reads a body with no runner or no hub link as null', () => {
    expect(fromRunnerStatusView(undefined)).toBeNull();
    expect(fromRunnerStatusView({} as never)).toBeNull();
  });
});

describe('fromDashboardView', () => {
  it('carries the fleet counts, harness availability and the runner status', () => {
    const dashboard = fromDashboardView(
      runnerDashboardView({
        fleet_summary: { needs: 1, ready: 2, running: 3, waiting: 4 },
        harness_health: {
          items: [{ harness_id: 'opencode', version: null, available: false, admitted_range: null, cause: 'binary missing', degradations: [] }],
        },
      }),
    );
    expect(dashboard.fleet_summary).toEqual({ needs: 1, ready: 2, running: 3, waiting: 4 });
    expect(dashboard.harness_health).toEqual([
      { harness_id: 'opencode', version: null, available: false, admitted_range: null, cause: 'binary missing', degradations: [] },
    ]);
    expect(dashboard.runner?.runner_id).toBe('rn_1');
  });

  it('keeps the fleet counts absent through a hub outage', () => {
    expect(fromDashboardView(runnerDashboardView({ fleet_summary: null })).fleet_summary).toBeNull();
  });
});
