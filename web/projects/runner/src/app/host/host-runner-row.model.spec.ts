import type { runnerApi } from 'fleet';

import type { MachineChunkRow } from '../core/chunk-rows.model';
import type { PanelLease, PanelProject } from '../core/panel-reads.model';
import { type HostRunnerReads, hostRunnerRows } from './host-runner-row.model';

const RUNNER = (overrides: Partial<runnerApi.HostRunnerView> = {}): runnerApi.HostRunnerView => ({
  runner_id: 'rn_1',
  name: 'r1',
  state: 'serving',
  served: true,
  halted_reasons: [],
  pause: { local: false, hub: false, effective: false, local_reason: null },
  throttle: null,
  bindings: [{ project: 'console', workspace_id: 'ws', agents: 4 }],
  agents_in_use: 1,
  max_agents: 4,
  ...overrides,
});

const STATUS = (overrides: Partial<runnerApi.RunnerStatusView> = {}): runnerApi.RunnerStatusView =>
  ({
    runner_id: 'rn_1',
    workspace_id: 'ws',
    pause: { local: false, hub: false, effective: false, local_reason: null },
    capacities: { used: 3, agents: 4, free: 1, bindings: [{ project: 'console', project_id: 'proj_c', workspace_id: 'ws', agents: 4, used: 3, free: 1 }] },
    hub: { endpoint: 'http://hub', reachable: true, last_contact_at: '2026-07-16T11:59:00.000Z', buffer_depth: 0, hub_version: null, window: null },
    last_tick_at: '2026-07-16T11:59:26.000Z',
    gates: [],
    ...overrides,
  }) as runnerApi.RunnerStatusView;

const DASHBOARD = (
  runner: runnerApi.RunnerStatusView,
  harness: runnerApi.HarnessHealthView[] = [],
): runnerApi.DashboardView =>
  ({ runner, harness_health: { items: harness } }) as unknown as runnerApi.DashboardView;

const CONSOLE: PanelProject = { slug: 'console', name: 'Console', entries: ['console', 'proj_c'], color: 'amber' };

const CHUNK = (chunkId: string, runnerId: string, lane: MachineChunkRow['lane']): MachineChunkRow =>
  ({
    lease: {
      chunk_id: chunkId,
      node_name: 'build',
      owner: { runnerId, name: runnerId, label: runnerId },
      projectSlug: 'console',
    } as unknown as PanelLease,
    leases: [],
    status: { label: 'RUNNING', tone: 'running' },
    live: null,
    lane,
    projectName: 'Console',
    projectColor: 'amber',
  }) as MachineChunkRow;

function reads(overrides: Partial<HostRunnerReads> = {}): HostRunnerReads {
  return { dashboards: new Map(), projects: new Map(), subscriptions: [], chunks: [], chunksPending: false, ...overrides };
}

function one(runner: runnerApi.HostRunnerView, status?: runnerApi.RunnerStatusView, extra: Partial<HostRunnerReads> = {}) {
  return hostRunnerRows(
    [runner],
    reads({ dashboards: status ? new Map([['rn_1', DASHBOARD(status)]]) : new Map(), ...extra }),
  )[0];
}

describe('hostRunnerRows', () => {
  it('shows a read-only declining floor from the host before the runner dashboard arrives', () => {
    const row = one(RUNNER({ throttle: {
      state: 'declining', declining: ['floor'], floor: { subscription: 'anthropic', admits: false, clear: null,
        hold: { kind: 'usage limit', harness_id: null, window: null, expected_reset: '2026-10-22T14:00:00Z', awaits_sample: false, grace_opens_at: null, lease_id: null } },
      levers: [], on_decline: 'hold', on_usage_limit: 'hold',
    } }));
    expect(row.throttle?.summary).toBe('declining — floor, resets 2026-10-22T14:00:00Z');
    expect(row.throttle?.lines[0].text).toContain('anthropic: exhausted');
  });

  it('reads a halted runner red, with only its HALTED badge carrying the reasons', () => {
    const row = one(RUNNER({ runner_id: null, state: 'halted', halted_reasons: ['no token', 'no hub'] }));

    expect(row.tone).toBe('red');
    expect(row.halted).toBe(true);
    expect(row.key).toBe('r1#0');
    expect(row.ref).toBeNull();
    expect(row.state).toEqual([{ label: 'HALTED', tone: 'needs', title: 'no token; no hub' }]);
  });

  it('reads a runner cut off from its hub red, badged HUB UNREACHABLE', () => {
    const row = one(RUNNER(), STATUS({ hub: { endpoint: 'http://hub', reachable: false, last_contact_at: null, buffer_depth: 3, hub_version: null, window: null } }));

    expect(row.tone).toBe('red');
    expect(row.state).toEqual([{ label: 'HUB UNREACHABLE', tone: 'stale', title: 'http://hub' }]);
    expect(row.hub).toEqual({ endpoint: 'http://hub', reachable: false, lastContactAt: null, buffered: 3, refusal: null, hubVersion: null, window: null });
  });

  it('reads a runner whose identity the hub refuses red, naming the refusal', () => {
    const row = one(
      RUNNER(),
      STATUS({ identity: { state: 'refused', reason: 'token revoked', reset_command: 'set the token' } as runnerApi.IdentityVerdictView }),
    );

    expect(row.tone).toBe('red');
    expect(row.hub?.refusal).toEqual({ label: 'REFUSED', tone: 'needs', title: 'token revoked — set the token' });
  });

  it('reads a runner the hub\'s version window refuses red, with the hub\'s sentence naming the upgrade', () => {
    const reason = "runner 0.1.4 is older than this hub's oldest supported line, 0.2; upgrade the runner host to 0.2 or later, up to 0.3";
    const row = one(RUNNER(), STATUS({ identity: { state: 'version_refused', reason } as runnerApi.IdentityVerdictView }));

    expect(row.tone).toBe('red');
    expect(row.hub?.refusal).toEqual({ label: 'VERSION REFUSED', tone: 'needs', title: reason });
  });

  it('carries the hub\'s version and the window it admits once a registration has answered', () => {
    const answered = one(
      RUNNER(),
      STATUS({
        hub: { endpoint: 'http://hub', reachable: true, last_contact_at: null, buffer_depth: 0, hub_version: '0.3.0', window: { floor: '0.2', ceiling: '0.3' } },
      }),
    );
    expect(answered.hub?.hubVersion).toBe('0.3.0');
    expect(answered.hub?.window).toBe('admits 0.2 – 0.3');

    expect(one(RUNNER(), STATUS()).hub?.hubVersion).toBeNull();
  });

  it('reads either brake amber, badging which side set it', () => {
    const local = one(RUNNER(), STATUS({ pause: { local: true, hub: false, effective: true, local_reason: 'usage limit' } }));
    expect(local.tone).toBe('amber');
    expect(local.state).toEqual([{ label: 'PAUSED HERE', tone: 'idle', title: 'usage limit' }]);

    const hub = one(RUNNER(), STATUS({ pause: { local: false, hub: true, effective: true, local_reason: null } }));
    expect(hub.tone).toBe('amber');
    expect(hub.state).toEqual([{ label: 'HUB PAUSED', tone: 'waiting', title: 'Paused by the hub — no new claims' }]);
  });

  it('falls back to the host runner list brake before the dashboard lands', () => {
    const row = one(RUNNER({ pause: { local: false, hub: true, effective: true, local_reason: null } }));

    expect(row.tone).toBe('amber');
    expect(row.state.map((badge) => badge.label)).toEqual(['HUB PAUSED']);
  });

  it('reads an unbraked runner green, WORKING while it holds a chunk and IDLE otherwise', () => {
    const idle = one(RUNNER(), STATUS());
    expect(idle.tone).toBe('green');
    expect(idle.state).toEqual([{ label: 'IDLE', tone: 'idle', title: null }]);

    const working = one(RUNNER(), STATUS(), { chunks: [CHUNK('ch_a', 'rn_1', 'progress')] });
    expect(working.state).toEqual([{ label: 'WORKING', tone: 'running', title: null }]);
  });

  it('claims neither WORKING nor IDLE while its chunks load, but still shows its brakes', () => {
    const loading = one(RUNNER(), STATUS(), { chunksPending: true });
    expect(loading.state).toEqual([]);
    expect(loading.heldPending).toBe(true);

    const paused = one(RUNNER(), STATUS({ pause: { local: false, hub: true, effective: true, local_reason: null } }), { chunksPending: true });
    expect(paused.state.map((badge) => badge.label)).toEqual(['HUB PAUSED']);
  });

  it('resolves each binding to its project by entry, with its colour and the agents its capacity says are used', () => {
    const row = one(RUNNER({ bindings: [{ project: 'proj_c', workspace_id: 'ws', agents: 4 }] }), STATUS({
      capacities: { used: 3, agents: 4, free: 1, bindings: [{ project: 'proj_c', project_id: 'proj_c', workspace_id: 'ws', agents: 4, used: 3, free: 1 }] },
    }), { projects: new Map([['rn_1', [CONSOLE]]]) });

    expect(row.bindings).toEqual([
      { key: 'proj_c@ws', projectName: 'Console', color: 'amber', workspaceId: 'ws', used: 3, agents: 4 },
    ]);
  });

  it('names a binding no served project accounts for by its entry, uncoloured, with none used', () => {
    const row = one(RUNNER({ bindings: [{ project: 'ops', workspace_id: 'ws2', agents: 2 }] }));

    expect(row.bindings).toEqual([{ key: 'ops@ws2', projectName: 'ops', color: null, workspaceId: 'ws2', used: 0, agents: 2 }]);
  });

  it('lists the subscriptions the runner spends by name', () => {
    const row = one(RUNNER(), undefined, {
      subscriptions: [
        { slug: 'max', name: 'Claude Max', spenders: ['rn_1'] },
        { slug: 'pro', name: 'ChatGPT Pro', spenders: ['rn_2'] },
      ] as runnerApi.HostSubscriptionView[],
    });

    expect(row.spends).toEqual(['Claude Max']);
  });

  it('carries each harness as its health check found it, and names them in the summary', () => {
    const rows = hostRunnerRows(
      [RUNNER({ max_agents: 24, agents_in_use: 7 })],
      reads({
        dashboards: new Map([
          [
            'rn_1',
            DASHBOARD(STATUS(), [
              { harness_id: 'claude_code', version: '2.1.0', available: true, admitted_range: '>=2.1', degradations: ['no images'], cause: null },
              { harness_id: 'opencode', version: null, available: false, admitted_range: null, cause: 'binary missing', degradations: [] },
            ]),
          ],
        ]),
      }),
    );

    expect(rows[0].harnesses).toEqual([
      { harnessId: 'claude_code', name: 'claude code', version: '2.1.0', available: true, admitted: '>=2.1', cause: null, degradations: ['no images'] },
      { harnessId: 'opencode', name: 'opencode', version: null, available: false, admitted: null, cause: 'binary missing', degradations: [] },
    ]);
    expect(rows[0].summary).toBe('claude code · opencode · 24 agents · 7 busy');
  });

  it('reads a one-agent runner singular, and no summary with neither harnesses nor agents', () => {
    expect(one(RUNNER({ max_agents: 1, agents_in_use: 0 })).summary).toBe('1 agent · 0 busy');
    expect(one(RUNNER({ max_agents: 0 })).summary).toBeNull();
  });

  it('writes one problem line per conflicting entry set and per environment held under no binding', () => {
    const row = one(
      RUNNER(),
      STATUS({
        routing_conflicts: [{ project_id: 'proj_c', entries: ['console', 'proj_c'] }],
        unmatched_bindings: [{ environment_id: 'e3', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9' }],
      } as Partial<runnerApi.RunnerStatusView>),
    );

    expect(row.problems).toHaveLength(2);
    expect(row.problems[0]).toBe('console, proj_c all name proj_c — claiming nothing until one is removed');
    expect(row.problems[1]).toMatch(/^e3 holds \S+ under no binding this runner declares$/);
  });

  it('holds only the runner’s own chunks that are not done', () => {
    const row = one(RUNNER(), STATUS(), {
      chunks: [CHUNK('ch_a', 'rn_1', 'progress'), CHUNK('ch_b', 'rn_1', 'done'), CHUNK('ch_c', 'rn_2', 'needs'), CHUNK('ch_d', 'rn_1', 'needs')],
    });

    expect(row.held.map((chunk) => chunk.chunkId)).toEqual(['ch_a', 'ch_d']);
    expect(row.held[0]).toMatchObject({ projectName: 'Console', color: 'amber', node: 'build', status: { label: 'RUNNING', tone: 'running' } });
  });

  it('carries the last tick and gates off the dashboard', () => {
    const row = one(RUNNER(), STATUS({ gates: ['review'] }));

    expect(row.lastTickAt).toBe('2026-07-16T11:59:26.000Z');
    expect(row.gates).toEqual(['review']);
    expect(row.key).toBe('rn_1');
  });
});
