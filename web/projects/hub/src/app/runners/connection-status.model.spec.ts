import { RunnerConnection, type RunnerRegistryView } from 'fleet';

import { connectionState, hostLight, runnerLight, runnerStateBadges, runnerTitleState } from './connection-status.model';
import type { RunnerRow } from './runner-rows.model';

const REGISTRY: RunnerRegistryView = {
  agents: null,
  runner_id: '',
  runner_name: '',
  added_at: '',
  added_by: null,
  bindings: null,
  capabilities: [],
  connection: RunnerConnection.NEVER_CONNECTED,
  env_capacity: null,
  gates: [],
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
  connection: RunnerConnection.ONLINE,
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
    expect(runnerLight(row({ online: false, hub_paused: true, connection: RunnerConnection.OFFLINE }))).toEqual({
      kind: 'lit',
      tone: 'red',
      blink: 'step',
    });
    expect(runnerLight(row({ online: false, connection: RunnerConnection.NEVER_CONNECTED }))).toEqual({ kind: 'never' });
  });
});

describe('hostLight', () => {
  it('reads a host on the runner ladder with no pause', () => {
    expect(hostLight(true, RunnerConnection.ONLINE)).toEqual({ kind: 'lit', tone: 'green', blink: 'hold' });
    expect(hostLight(false, RunnerConnection.OFFLINE)).toEqual({ kind: 'lit', tone: 'red', blink: 'step' });
    expect(hostLight(false, RunnerConnection.NEVER_CONNECTED)).toEqual({ kind: 'never' });
  });
});

describe('connectionState', () => {
  it('reads online, offline and never connected, each in its tone', () => {
    expect(connectionState(true, RunnerConnection.ONLINE)).toEqual({ label: 'ONLINE', tone: 'done' });
    expect(connectionState(false, RunnerConnection.OFFLINE)).toEqual({ label: 'OFFLINE', tone: 'stale' });
    expect(connectionState(false, RunnerConnection.NEVER_CONNECTED)).toEqual({ label: 'NEVER CONNECTED', tone: 'idle' });
  });
});

describe('runnerStateBadges', () => {
  const labels = (r: RunnerRow) => runnerStateBadges(r).map((badge) => badge.label);

  it('reads an online runner as IDLE, or WORKING while it holds a chunk', () => {
    expect(labels(row())).toEqual(['IDLE']);
    expect(labels(row({ claims: [{ chunkId: 'c', shortId: 'c', node: 'build', status: 'running' }] }))).toEqual(['WORKING']);
  });

  it('names an offline or never-connected runner by its connection', () => {
    expect(labels(row({ online: false, connection: RunnerConnection.OFFLINE }))).toEqual(['OFFLINE']);
    expect(labels(row({ online: false, connection: RunnerConnection.NEVER_CONNECTED }))).toEqual(['NEVER CONNECTED']);
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
    expect(runnerTitleState(row({ online: false, hub_paused: true, connection: RunnerConnection.OFFLINE }))).toEqual({ label: 'OFFLINE', tone: 'stale' });
  });
});
