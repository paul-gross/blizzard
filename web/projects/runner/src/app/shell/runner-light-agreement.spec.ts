import { hostPanelRunners } from '../board/rail/host-panel.model';
import { hostRunner } from '../core/host-runner.builders';
import type { HostRunner } from '../core/host-runner.domain';
import { pauseState } from '../core/pause-state.builders';
import { dashboardStatus, hubConnectivity, identityVerdict, runnerCapacities, runnerStatus } from '../core/runner-status.builders';
import type { KnownIdentityState, RunnerStatus } from '../core/runner-status.domain';
import { hostRunnerRows } from '../host/host-runner-row.model';

const KNOWN: readonly string[] = ['unchecked', 'matched', 'mismatched', 'refused', 'not_held', 'tenant_differs', 'version_refused'];

/**
 * The rail and the host page light one runner the same: both read the one `runnerLight`, so
 * one runner and its status can never be lit two ways. Lives in the shell, the one unit
 * allowed to import both `board` and `host` (`bzh:frontend-package-layers`).
 */

const PAUSE = (local: boolean, hub: boolean) => pauseState({ local, hub });

const RUNNER = (overrides: Partial<HostRunner> = {}): HostRunner => hostRunner({ pause: PAUSE(false, false), max_agents: 4, ...overrides });

const STATUS = (overrides: Partial<RunnerStatus> = {}): RunnerStatus =>
  runnerStatus({
    hub: hubConnectivity({ endpoint: 'http://hub' }),
    pause: PAUSE(false, false),
    capacities: runnerCapacities({ agents: 4, free: 4 }),
    ...overrides,
  });

function lit(runner: HostRunner, status: RunnerStatus | undefined) {
  const id = runner.runner_id ?? '';
  const rail = hostPanelRunners([runner], new Map(status ? [[id, status]] : []))[0].tone;
  const dashboards = new Map(status ? [[id, dashboardStatus({ runner: status })]] : []);
  const row = hostRunnerRows([runner], { dashboards, projects: new Map(), subscriptions: [], chunks: [], chunksPending: false })[0].tone;
  return { rail, row };
}

describe('the rail and the host row light one runner alike', () => {
  const identity = (state: string) =>
    identityVerdict({ state: KNOWN.includes(state) ? (state as KnownIdentityState) : { kind: 'unknown', raw: state }, reset_command: 'reset' });

  it.each([
    ['healthy', RUNNER(), STATUS(), 'green'],
    ['halted', RUNNER({ state: 'halted' }), undefined, 'red'],
    ['paused here', RUNNER(), STATUS({ pause: PAUSE(true, false) }), 'amber'],
    ['paused by the hub', RUNNER(), STATUS({ pause: PAUSE(false, true) }), 'amber'],
    ['hub unreachable', RUNNER(), STATUS({ hub: hubConnectivity({ endpoint: 'http://hub', reachable: false }) }), 'red'],
    ['identity refused', RUNNER(), STATUS({ identity: identity('refused') }), 'red'],
    ['version refused', RUNNER(), STATUS({ identity: identity('version_refused') }), 'red'],
    ['identity unknown', RUNNER(), STATUS({ identity: identity('quarantined') }), 'green'],
    ['held by effective alone', RUNNER(), STATUS({ pause: pauseState({ effective: true }) }), 'amber'],
    ['braked but not effective', RUNNER(), STATUS({ pause: pauseState({ local: true, effective: false }) }), 'green'],
  ])('%s', (_name, runner, status, expected) => {
    expect(lit(runner, status)).toEqual({ rail: expected, row: expected });
  });
});
