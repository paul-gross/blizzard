import type { HubRunner, RunnerDeclining } from '../core/runners.domain';
import { formatWhen } from 'fleet';

import { connectionState, hostLight, runnerLight, runnerStateBadges, runnerTitleState } from './connection-status.model';
import type { RunnerRow } from './runner-rows.model';

it('shows an unrecognized connection label without claiming a known state', () => {
  expect(connectionState(false, { kind: 'unknown', raw: 'reconnecting' })).toEqual({ label: 'reconnecting', tone: 'idle' });
  expect(connectionState(true, { kind: 'unknown', raw: 'reconnecting' })).toEqual({ label: 'reconnecting', tone: 'idle' });
});

const REGISTRY: HubRunner = {
  agents: null,
  runner_id: '',
  runner_name: '',
  added_at: '',
  added_by: null,
  bindings: null,
  capabilities: [],
  connection: 'never_connected',
  declining: [],
  env_capacity: null,
  gates: [],
  unenforceable_gates: [],
  host_id: null,
  hub_paused: false,
  last_seen_at: null,
  locally_paused: false,
  locally_paused_by: null,
  locally_paused_reason: null,
  online: false,
  projects: [],
  registered_at: null,
  retired: false,
  retired_at: null,
  retired_by: null,
  subscription: null,
  subscription_unrostered: false,
  subscriptions: [],
  version: null,
  version_refusal: null,
  version_standing: null,
  workspace_id: null,
};


const NOW = new Date().toISOString();

const row = (over: Partial<RunnerRow> = {}): RunnerRow => ({
  ...REGISTRY,
  runner_id: 'r',
  runner_name: 'r-name',
  added_at: NOW,
  connection: 'online',
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  hub_paused: false,
  locally_paused: false,
  claims: [],
  used: 0,
  subscriptionPaces: [],
  nowMs: Date.parse(NOW),
  served: [],
  unassigned: false,
  unresolved: [],
  bindingLines: [],
  subscriptionName: null,
  ...over,
});

describe('runnerLight', () => {
  it('reads offline over a pause, a pause over online, and a never-connected runner as unlit', () => {
    expect(runnerLight(row())).toEqual({ kind: 'lit', tone: 'green', blink: 'hold' });
    expect(runnerLight(row({ locally_paused: true }))).toEqual({ kind: 'lit', tone: 'amber', blink: 'hold' });
    expect(runnerLight(row({ hub_paused: true }))).toEqual({ kind: 'lit', tone: 'amber', blink: 'hold' });
    expect(runnerLight(row({ online: false, hub_paused: true, connection: 'offline' }))).toEqual({
      kind: 'lit',
      tone: 'red',
      blink: 'step',
    });
    expect(runnerLight(row({ online: false, connection: 'never_connected' }))).toEqual({ kind: 'never' });
  });
});

describe('hostLight', () => {
  it('reads a host on the runner ladder with no pause', () => {
    expect(hostLight(true, 'online')).toEqual({ kind: 'lit', tone: 'green', blink: 'hold' });
    expect(hostLight(false, 'offline')).toEqual({ kind: 'lit', tone: 'red', blink: 'step' });
    expect(hostLight(false, 'never_connected')).toEqual({ kind: 'never' });
  });
});

describe('connectionState', () => {
  it('reads online, offline and never connected, each in its tone', () => {
    expect(connectionState(true, 'online')).toEqual({ label: 'ONLINE', tone: 'done' });
    expect(connectionState(false, 'offline')).toEqual({ label: 'OFFLINE', tone: 'stale' });
    expect(connectionState(false, 'never_connected')).toEqual({ label: 'NEVER CONNECTED', tone: 'idle' });
  });
});

describe('runnerStateBadges', () => {
  const labels = (r: RunnerRow) => runnerStateBadges(r).map((badge) => badge.label);

  it('reads an online runner as IDLE, or WORKING while it holds a chunk', () => {
    expect(labels(row())).toEqual(['IDLE']);
    expect(labels(row({ claims: [{ chunkId: 'c', shortId: 'c', node: 'build', status: { kind: 'running' as const } }] }))).toEqual(['WORKING']);
  });

  describe('when a throttle says no', () => {
    const floor: RunnerDeclining = {
      lever: 'floor',
      lever_kind: 'floor',
      detail: 'exhausted',
      subscription: 'anthropic',
      until: NOW,
    };
    const pace: RunnerDeclining = {
      lever: 'pace',
      lever_kind: 'pace',
      detail: '7d 86% spent, 71% elapsed',
      subscription: null,
      until: null,
    };
    const reset = formatWhen(NOW, new Date(NOW));

    it('words the floor with its subscription and reset, in place of IDLE', () => {
      expect(labels(row({ declining: [floor] }))).toEqual([`waiting on anthropic — exhausted, resets ${reset}`]);
    });

    it('words a lever, or one this board has no name for, in the generic form', () => {
      expect(labels(row({ declining: [pace] }))).toEqual(['declining — pace: 7d 86% spent, 71% elapsed']);
      expect(labels(row({ declining: [{ ...pace, lever: 'from-a-newer-runner', lever_kind: { kind: 'unknown', raw: 'from-a-newer-runner' }, detail: 'd' }] }))).toEqual([
        'declining — from-a-newer-runner: d',
      ]);
    });

    it('shows the first reason, in the gate\'s order, and lists every one in its title', () => {
      const [badge] = runnerStateBadges(row({ declining: [pace, floor] }));

      expect(badge.label).toBe('declining — pace: 7d 86% spent, 71% elapsed');
      expect(badge.title).toContain(`waiting on anthropic — exhausted, resets ${reset}`);
      expect(badge.testid).toBe('fleet-runner-declining');
    });

    it('is hidden behind either brake, and for a runner that is offline', () => {
      expect(labels(row({ declining: [floor], hub_paused: true }))).toEqual(['HUB PAUSED']);
      expect(labels(row({ declining: [floor], locally_paused: true }))).toEqual(['LOCALLY PAUSED']);
      expect(labels(row({ declining: [floor], online: false, connection: 'offline' }))).toEqual(['OFFLINE']);
    });
  });

  it('names an offline or never-connected runner by its connection', () => {
    expect(labels(row({ online: false, connection: 'offline' }))).toEqual(['OFFLINE']);
    expect(labels(row({ online: false, connection: 'never_connected' }))).toEqual(['NEVER CONNECTED']);
  });

  it('stacks each brake, the local one carrying its hint and the hub one its test hook', () => {
    const badges = runnerStateBadges(row({ locally_paused: true, hub_paused: true, locally_paused_reason: 'spend cap' }));

    expect(badges.map((badge) => badge.label)).toEqual(['LOCALLY PAUSED', 'HUB PAUSED']);
    expect(badges[0].title).toBe('spend cap');
    expect(badges[1].testid).toBe('fleet-runner-hub-paused');
  });
});

describe('runnerTitleState', () => {
  it('lets a brake on an online runner outrank ONLINE, as its light does', () => {
    expect(runnerTitleState(row())).toEqual({ label: 'ONLINE', tone: 'done' });
    expect(runnerTitleState(row({ hub_paused: true }))).toEqual({ label: 'HUB PAUSED', tone: 'waiting' });
    expect(runnerTitleState(row({ locally_paused: true }))).toEqual({ label: 'LOCALLY PAUSED', tone: 'idle' });
    expect(runnerTitleState(row({ online: false, hub_paused: true, connection: 'offline' }))).toEqual({ label: 'OFFLINE', tone: 'stale' });
  });
});
