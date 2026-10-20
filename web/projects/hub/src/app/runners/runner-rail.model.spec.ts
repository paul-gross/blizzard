import { hubApi, type RunnerConnection, type SubscriptionPace } from 'fleet';

import type { HostCard, HostGroups } from './host-rows.model';
import { agentGauges, envGauges, railVm, subscriptionLines } from './runner-rail.model';
import type { RunnerRow } from './runner-rows.model';

const card = (id: string, workspaces: { capacity: number | null; inUse: number }[]): HostCard =>
  ({
    hostId: id,
    hostName: `${id}-name`,
    subscriptionPaces: [],
    workspaces: workspaces.map((ws, i) => ({ workspaceId: `${id}-ws${i}`, flag: null, environments: [], ...ws })),
  }) as unknown as HostCard;

const row = (id: string, over: Partial<RunnerRow> = {}): RunnerRow =>
  ({
    runner_id: id,
    runner_name: `${id}-name`,
    online: true,
    hub_paused: false,
    locally_paused: false,
    workspace_id: null,
    connection: 'online' as RunnerConnection,
    used: 0,
    env_capacity: null,
    bindingLines: [],
    subscriptionPaces: [],
    ...over,
  }) as unknown as RunnerRow;

const binding = (project: string, agents: number, agentsInUse: number) => ({
  project,
  workspaceId: 'w',
  agents,
  agentsInUse,
  unreported: false,
});

const groups = (cards: HostGroups<RunnerRow>['cards'], hostless: RunnerRow[] = []): HostGroups<RunnerRow> => ({ cards, hostless });

describe('envGauges', () => {
  it('draws one gauge per host workspace, then one per host-less pool, each named for its workspace', () => {
    const gauges = envGauges(
      groups([{ card: card('h1', [{ capacity: 3, inUse: 2 }, { capacity: 2, inUse: 0 }]), rows: [] }], [
        row('solo', { env_capacity: 4, used: 1, workspace_id: 'ws-solo' }),
      ]),
    );

    expect(gauges).toEqual([
      { name: 'h1-ws0', used: 2, total: 3 },
      { name: 'h1-ws1', used: 0, total: 2 },
      { name: 'ws-solo', used: 1, total: 4 },
    ]);
  });

  it('names a pool with no workspace id for its host, or a host-less one for its runner', () => {
    const nameless = { ...card('h1', []), workspaces: [{ workspaceId: null, capacity: 1, inUse: 0, flag: null, environments: [] }] } as HostCard;

    expect(envGauges(groups([{ card: nameless, rows: [] }], [row('solo', { env_capacity: 2 })])).map((g) => g.name)).toEqual([
      'h1-name',
      'solo-name',
    ]);
  });

  it('draws no gauge for a pool reporting a null or zero capacity', () => {
    const gauges = envGauges(
      groups([{ card: card('h1', [{ capacity: null, inUse: 5 }, { capacity: 0, inUse: 0 }, { capacity: 2, inUse: 1 }]), rows: [] }], [
        row('old', { env_capacity: null, used: 3 }),
      ]),
    );

    expect(gauges).toEqual([{ name: 'h1-ws2', used: 1, total: 2 }]);
  });

  it('does not draw a hosted runner’s own capacity, which its host workspaces already carry', () => {
    const hosted = row('rn', { env_capacity: 8, used: 4 });

    expect(envGauges(groups([{ card: card('h1', [{ capacity: 2, inUse: 1 }]), rows: [hosted] }]))).toEqual([
      { name: 'h1-ws0', used: 1, total: 2 },
    ]);
  });
});

describe('agentGauges', () => {
  it('draws one gauge per binding — its project’s agents busy of those it allows, never summed', () => {
    expect(agentGauges(row('r', { bindingLines: [binding('blizzard', 2, 2), binding('winter', 1, 0)] }))).toEqual([
      { name: 'blizzard', used: 2, total: 2 },
      { name: 'winter', used: 0, total: 1 },
    ]);
  });

  it('is empty for a runner declaring no bindings', () => {
    expect(agentGauges(row('r'))).toEqual([]);
  });
});

describe('railVm', () => {
  it('lists hosted runners in card order, then the host-less, each with its agents', () => {
    const a = row('a', { bindingLines: [binding('p', 1, 0)] });
    const b = row('b');
    const vm = railVm(groups([{ card: card('h1', [{ capacity: 1, inUse: 0 }]), rows: [a] }], [b]));

    expect(vm.rows.map((entry) => entry.row.runner_id)).toEqual(['a', 'b']);
    expect(vm.rows.map((entry) => [entry.hostName, entry.fleetId])).toEqual([
      ['h1-name', 'h1'],
      [null, 'b'],
    ]);
    expect(vm.rows.map((entry) => entry.agents)).toEqual([[{ name: 'p', used: 0, total: 1 }], []]);
    expect(vm.envs).toEqual([{ name: 'h1-ws0', used: 0, total: 1 }]);
    expect(vm.subscriptions).toEqual([]);
  });
});

const pace = (slug: string, over: Partial<SubscriptionPace> = {}): SubscriptionPace => ({
  slug,
  name: `${slug}-name`,
  paceBars: [
    { window: '5h', utilizationPct: 62, elapsedPct: 70 },
    { window: '7d', utilizationPct: 80, elapsedPct: 41 },
  ],
  condition: null,
  sampledAt: '2026-07-16T12:00:00.000Z',
  refreshedLabel: null,
  freshness: 'fresh',
  missReason: null,
  ...over,
});

const withPaces = (id: string, paces: SubscriptionPace[]): HostCard => ({ ...card(id, []), subscriptionPaces: paces }) as HostCard;

describe('subscriptionLines', () => {
  it('draws each window as ten blocks filled to its usage, its clock a notch, ahead when usage passes the clock', () => {
    const [line] = subscriptionLines(groups([{ card: withPaces('h1', [pace('claude')]), rows: [] }]));

    expect(line.state).toBe('paced');
    const [five, seven] = line.windows;
    expect(five.cells.map((c) => c.on)).toEqual([true, true, true, true, true, true, false, false, false, false]);
    expect(five.cells.findIndex((c) => c.clockBefore)).toBe(7);
    expect(five.ahead).toBe(false);
    expect(five.hint).toBe('5h · 62% used · 70% of window');
    expect(seven.cells.filter((c) => c.on)).toHaveLength(8);
    expect(seven.ahead).toBe(true);
  });

  it('marks a window whose clock has run out after its last block', () => {
    const [line] = subscriptionLines(
      groups([{ card: withPaces('h1', [pace('c', { paceBars: [{ window: '5h', utilizationPct: 10, elapsedPct: 100 }] })]), rows: [] }]),
    );

    expect(line.windows[0].cells.some((c) => c.clockBefore)).toBe(false);
    expect(line.windows[0].cells.at(-1)?.clockAfter).toBe(true);
  });

  it('lists each subscription once across hosts and host-less runners, reading the freshest sample', () => {
    const lines = subscriptionLines(
      groups(
        [
          { card: withPaces('h1', [pace('claude', { sampledAt: '2026-07-16T11:00:00.000Z' }), pace('gpt')]), rows: [] },
          { card: withPaces('h2', [pace('claude', { sampledAt: '2026-07-16T11:30:00.000Z', freshness: 'stale' })]), rows: [] },
        ],
        [row('solo', { subscriptionPaces: [pace('gemini'), pace('claude', { sampledAt: null })] })],
      ),
    );

    expect(lines.map((l) => l.slug)).toEqual(['claude', 'gpt', 'gemini']);
    expect(lines[0].stale).toBe(true);
    expect(subscriptionLines(groups([{ card: withPaces('h1', [pace('c', { refreshedTag: '[50s]' }), pace('n')]), rows: [] }])).map((l) => l.age)).toEqual([
      '[50s]',
      null,
    ]);
  });

  it('reads a subscription lapsed when any host reports it lapsed, however fresh another host reads it', () => {
    const lapsed = pace('claude', { condition: hubApi.SampleMissReason.CREDENTIAL_LAPSED, sampledAt: '2026-07-16T10:00:00.000Z' });
    const good = pace('claude', { sampledAt: '2026-07-16T12:00:00.000Z' });

    for (const order of [
      [lapsed, good],
      [good, lapsed],
    ]) {
      const lines = subscriptionLines(groups(order.map((p, i) => ({ card: withPaces(`h${i}`, [p]), rows: [] }))));
      expect(lines.map((l) => l.state)).toEqual(['lapsed']);
    }
  });

  it('reads a lapsed credential as lapsed, and no sample or no windows as unsampled', () => {
    const lines = subscriptionLines(
      groups([
        {
          card: withPaces('h1', [
            pace('lapsed', { condition: hubApi.SampleMissReason.CREDENTIAL_LAPSED }),
            pace('never', { sampledAt: null, paceBars: [] }),
            pace('empty', { paceBars: [] }),
          ]),
          rows: [],
        },
      ]),
    );

    expect(lines.map((l) => [l.state, l.windows.length])).toEqual([
      ['lapsed', 0],
      ['unsampled', 0],
      ['unsampled', 0],
    ]);
  });
});
