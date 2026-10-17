import { type HostRegistryView, RunnerConnection, type RunnerRegistryView } from 'fleet';
import type { ChunkSummary } from 'fleet/shell';

import { bindingLines, connectionState, foldHostCards, groupHostRows, subscriptionName } from './host-rows.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');
const SEEN = '2026-07-16T11:59:48.000Z';

function runner(id: string, over: Partial<RunnerRegistryView> = {}): RunnerRegistryView {
  return { runner_id: id, runner_name: `${id}-name`, projects: [], ...over } as unknown as RunnerRegistryView;
}

function chunk(id: string, runnerId: string | null, project: { id?: string; slug?: string }, envs = 1): ChunkSummary {
  return {
    chunk_id: id,
    runner_id: runnerId,
    project_id: project.id ?? null,
    project_slug: project.slug ?? null,
    environment_count: envs,
  } as unknown as ChunkSummary;
}

function host(id: string, over: Partial<HostRegistryView> = {}): HostRegistryView {
  return {
    host_id: id,
    host_name: `${id}-name`,
    connection: 'online',
    online: true,
    last_seen_at: SEEN,
    workspaces: [],
    subscription_usage: [],
    subscriptions: [],
    ...over,
  } as unknown as HostRegistryView;
}

const SERVING = [
  { entry: 'default', project_id: 'pj_default', slug: 'default', name: 'Default' },
  { entry: 'pj_glacier', project_id: 'pj_glacier', slug: 'glacier', name: 'Glacier' },
];

describe('bindingLines', () => {
  it("names each binding's project from the runner's matching entry and counts its live chunks as agents in use", () => {
    const rA = runner('rA', {
      projects: SERVING,
      bindings: [
        { project: 'default', workspace_id: 'ws-1', agents: 3 },
        { project: 'pj_glacier', workspace_id: 'ws-9', agents: 1, workspace_unreported: true },
      ],
    });
    const lines = bindingLines(rA, [
      chunk('ch_a', 'rA', { id: 'pj_default', slug: 'default' }),
      chunk('ch_b', 'rA', { id: 'pj_default' }),
      chunk('ch_c', 'rB', { id: 'pj_default' }),
      chunk('ch_d', 'rA', { slug: 'glacier', id: 'pj_glacier' }),
    ]);

    expect(lines).toEqual([
      { project: 'Default', workspaceId: 'ws-1', agents: 3, agentsInUse: 2, unreported: false },
      { project: 'Glacier', workspaceId: 'ws-9', agents: 1, agentsInUse: 1, unreported: true },
    ]);
  });

  it('keeps the binding as the runner wrote it when no entry resolves it, and is empty for a host-less runner', () => {
    expect(bindingLines(runner('rA', { bindings: [{ project: 'gone', workspace_id: 'ws-1', agents: 2 }] }), [])).toEqual([
      { project: 'gone', workspaceId: 'ws-1', agents: 2, agentsInUse: 0, unreported: false },
    ]);
    expect(bindingLines(runner('rC'), [])).toEqual([]);
  });
});

describe('subscriptionName', () => {
  const hosts = new Map([['h1', host('h1', { subscriptions: [{ slug: 'anthropic', name: 'Anthropic Max', provider: 'claude' }] })]]);

  it("resolves the slug's name from the runner's host roster, falling back to the slug", () => {
    expect(subscriptionName(runner('rA', { host_id: 'h1', subscription: 'anthropic' }), hosts)).toBe('Anthropic Max');
    expect(subscriptionName(runner('rB', { host_id: 'h1', subscription: 'zzz' }), hosts)).toBe('zzz');
    expect(subscriptionName(runner('rC'), hosts)).toBeNull();
  });
});

describe('foldHostCards', () => {
  const H1 = host('h1', {
    workspaces: [{ workspace_id: 'ws-1', env_capacity: 3 }, { workspace_id: 'ws-2' }],
    subscription_usage: [
      { slug: 'anthropic', name: 'Anthropic', windows: [], sampled_at: SEEN, runners: ['rA'] },
      { slug: 'openai', name: 'OpenAI', windows: [], runners: [] },
    ],
  });
  const RUNNERS = [
    runner('rA', { host_id: 'h1', projects: SERVING, bindings: [{ project: 'default', workspace_id: 'ws-1', agents: 2 }] }),
    runner('rB', { host_id: 'h1', projects: SERVING, bindings: [{ project: 'pj_glacier', workspace_id: 'ws-1', agents: 1 }] }),
    runner('rC'),
  ];
  const CHUNKS = [chunk('ch_a', 'rA', { id: 'pj_default' }, 2), chunk('ch_b', 'rB', { id: 'pj_glacier' }, 1)];

  it("folds a host's report, workspaces with their in-use counts, and each subscription once naming its spenders", () => {
    const [card] = foldHostCards([H1], RUNNERS, null, CHUNKS, NOW);

    expect(card.hostId).toBe('h1');
    expect(card.hostName).toBe('h1-name');
    expect(card.reportLabel).toBe('seen 12s ago');
    expect(card.reportedAgo).toBe('12s ago');
    expect(card.workspaces.map((ws) => [ws.workspaceId, ws.inUse, ws.capacity])).toEqual([
      ['ws-1', 3, 3],
      ['ws-2', 0, null],
    ]);
    expect(card.subscriptionPaces.map((pace) => [pace.slug, pace.spenders])).toEqual([
      ['anthropic', ['rA-name']],
      ['openai', []],
    ]);
  });

  it('reads a never-reported host as such', () => {
    const [card] = foldHostCards([host('h2', { connection: 'never_connected', online: false, last_seen_at: null })], [], null, [], NOW);
    expect(card.reportLabel).toBe('never reported');
    expect(card.reportedAgo).toBeNull();
  });

  it('gives no age for a report stamped ahead of this clock or unreadable, rather than a confident 0s', () => {
    const hosts = [host('h3', { last_seen_at: '2026-07-16T17:00:00.000Z' }), host('h4', { last_seen_at: 'not-a-time' })];
    const [skewed, garbled] = foldHostCards(hosts, [], null, [], NOW);
    expect(skewed.reportedAgo).toBeNull();
    expect(garbled.reportedAgo).toBeNull();
  });

  it('shows every host on All, one with no runners included, and under a lens only hosts holding a lensed runner', () => {
    const empty = host('h_empty');
    expect(foldHostCards([H1, empty], RUNNERS, null, CHUNKS, NOW).map((card) => card.hostId)).toEqual(['h1', 'h_empty']);
    expect(foldHostCards([H1, empty], RUNNERS, [RUNNERS[0]], CHUNKS, NOW).map((card) => card.hostId)).toEqual(['h1']);
    expect(foldHostCards([H1, empty], RUNNERS, [RUNNERS[2]], CHUNKS, NOW)).toEqual([]);
  });
});

describe('connectionState', () => {
  it('reads online, offline and never connected, each in its tone', () => {
    expect(connectionState(true, RunnerConnection.ONLINE)).toEqual({ label: 'ONLINE', tone: 'done' });
    expect(connectionState(false, RunnerConnection.OFFLINE)).toEqual({ label: 'OFFLINE', tone: 'stale' });
    expect(connectionState(false, RunnerConnection.NEVER_CONNECTED)).toEqual({ label: 'NEVER CONNECTED', tone: 'idle' });
  });
});

describe('groupHostRows', () => {
  it('holds each hosted row under its card and every other row host-less', () => {
    const cards = foldHostCards([host('h1')], [], null, [], NOW);
    const rows = [runner('rA', { host_id: 'h1' }), runner('rC'), runner('rB', { host_id: 'h1' }), runner('rX', { host_id: 'h_gone' })];

    const groups = groupHostRows(rows, cards);

    expect(groups.cards).toHaveLength(1);
    expect(groups.cards[0].rows.map((row) => row.runner_id)).toEqual(['rA', 'rB']);
    expect(groups.hostless.map((row) => row.runner_id)).toEqual(['rC', 'rX']);
  });
});
