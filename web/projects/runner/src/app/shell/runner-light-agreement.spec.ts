import type { runnerApi } from 'fleet';

import { hostPanelRunners } from '../board/rail/host-panel.model';
import { hostRunnerRows } from '../host/host-runner-row.model';

/**
 * The rail and the host page light one runner the same: both read the one `runnerLight`, so
 * one runner and its status can never be lit two ways. Lives in the shell, the one unit
 * allowed to import both `board` and `host` (`bzh:frontend-package-layers`).
 */

const PAUSE = (local: boolean, hub: boolean) => ({ local, hub, effective: local || hub, local_reason: null });

const RUNNER = (overrides: Partial<runnerApi.HostRunnerView> = {}): runnerApi.HostRunnerView => ({
  runner_id: 'rn_1',
  name: 'r1',
  state: 'serving',
  served: true,
  halted_reasons: [],
  pause: PAUSE(false, false),
  throttle: null,
  bindings: [],
  agents_in_use: 0,
  max_agents: 4,
  ...overrides,
});

const STATUS = (overrides: Record<string, unknown> = {}): runnerApi.RunnerStatusView =>
  ({
    hub: { endpoint: 'http://hub', reachable: true, last_contact_at: null, buffer_depth: 0, hub_version: null, window: null },
    pause: PAUSE(false, false),
    identity: null,
    capacities: { used: 0, agents: 4, free: 4, bindings: [] },
    gates: [],
    throttle: null,
    ...overrides,
  }) as unknown as runnerApi.RunnerStatusView;

function lit(runner: runnerApi.HostRunnerView, status: runnerApi.RunnerStatusView | undefined) {
  const id = runner.runner_id ?? '';
  const rail = hostPanelRunners([runner], new Map(status ? [[id, status]] : []))[0].tone;
  const dashboards = new Map(status ? [[id, { runner: status } as runnerApi.DashboardView]] : []);
  const row = hostRunnerRows([runner], { dashboards, projects: new Map(), subscriptions: [], chunks: [], chunksPending: false })[0].tone;
  return { rail, row };
}

describe('the rail and the host row light one runner alike', () => {
  const identity = (state: string) => ({ state, reason: null, reset_command: 'reset', token_runner_id: null }) as runnerApi.IdentityVerdictView;

  it.each([
    ['healthy', RUNNER(), STATUS(), 'green'],
    ['halted', RUNNER({ state: 'halted' }), undefined, 'red'],
    ['paused here', RUNNER(), STATUS({ pause: PAUSE(true, false) }), 'amber'],
    ['paused by the hub', RUNNER(), STATUS({ pause: PAUSE(false, true) }), 'amber'],
    ['hub unreachable', RUNNER(), STATUS({ hub: { endpoint: 'http://hub', reachable: false, buffer_depth: 0 } }), 'red'],
    ['identity refused', RUNNER(), STATUS({ identity: identity('refused') }), 'red'],
    ['version refused', RUNNER(), STATUS({ identity: identity('version_refused') }), 'red'],
    ['identity unknown', RUNNER(), STATUS({ identity: identity('quarantined') }), 'green'],
  ])('%s', (_name, runner, status, expected) => {
    expect(lit(runner, status)).toEqual({ rail: expected, row: expected });
  });
});
