import { RunnerConnection, type WorkspaceRow } from 'fleet';

import { firstFleetId, fleetList, resolveSelection } from './fleet-page.model';
import type { HostCard, HostGroups } from './host-rows.model';
import type { RunnerRow } from './runner-rows.model';

function card(id: string, over: Partial<HostCard> = {}): HostCard {
  return {
    hostId: id,
    hostName: `box-${id}`,
    online: true,
    connection: RunnerConnection.ONLINE,
    reportLabel: 'reported 3s ago',
    workspaces: [],
    subscriptionPaces: [],
    url: 'https://box.example',
    enrolled: true,
    registeredAt: '2026-01-02T03:04:05Z',
    addedAt: '2026-01-01T00:00:00Z',
    addedBy: 'paul',
    lastSeenLabel: 'now',
    reportedAgo: '4s ago',
    heldChunks: new Map(),
    ...over,
  };
}

function row(id: string, bindings: { agents: number; agentsInUse: number }[] = []): RunnerRow {
  return {
    runner_id: id,
    runner_name: `name-${id}`,
    online: true,
    connection: RunnerConnection.ONLINE,
    bindingLines: bindings.map((b) => ({ project: 'p', workspaceId: 'ws', unreported: false, ...b })),
  } as unknown as RunnerRow;
}

const GROUPS: HostGroups<RunnerRow> = {
  cards: [
    { card: card('hs_AAAA1111'), rows: [row('rn_1', [{ agents: 2, agentsInUse: 1 }]), row('rn_2', [{ agents: 3, agentsInUse: 2 }])] },
    { card: card('hs_BBBB2222'), rows: [] },
  ],
  hostless: [row('rn_SOLO0001')],
};

describe('fleetList', () => {
  it('lists host cards in order, then hostless runners, with agent counts and short refs', () => {
    const list = fleetList(GROUPS);

    expect(list.hosts.map((c) => [c.id, c.agents, c.ref])).toEqual([
      ['hs_AAAA1111', 5, 'H-1111'],
      ['hs_BBBB2222', null, 'H-2222'],
    ]);
    expect(list.hostless.map((c) => [c.id, c.agents, c.ref])).toEqual([['rn_SOLO0001', null, 'R-0001']]);
  });
});

describe('resolveSelection', () => {
  it('selects nothing without an id', () => {
    expect(resolveSelection(GROUPS, null)).toBeNull();
  });

  it('resolves a host with its agents busy / declared and connection facts', () => {
    const sel = resolveSelection(GROUPS, 'hs_AAAA1111');

    expect(sel).toMatchObject({ kind: 'host', ref: 'H-1111', agentsDeclared: 5, agentsBusy: 3 });
    if (sel?.kind !== 'host') throw new Error('expected a host');
    expect(sel.facts.map((f) => f.label)).toEqual(['Registered', 'Added']);
  });

  it('totals the host’s environments in use over capacity, none while a workspace reports no capacity', () => {
    const ws = (workspaceId: string, capacity: number | null, inUse: number): WorkspaceRow => ({
      workspaceId,
      capacity,
      inUse,
      flag: null,
      environments: [],
    });
    const resolve = (workspaces: WorkspaceRow[]) => {
      const sel = resolveSelection({ cards: [{ card: card('hs_AAAA1111', { workspaces }), rows: [] }], hostless: [] }, 'hs_AAAA1111');
      if (sel?.kind !== 'host') throw new Error('expected a host');
      return [sel.envsInUse, sel.envsCapacity];
    };

    expect(resolve([ws('ws_a', 3, 1), ws('ws_b', 2, 1)])).toEqual([2, 5]);
    expect(resolve([ws('ws_a', 3, 1), ws('ws_b', null, 1)])).toEqual([2, null]);
  });

  it('resolves a hostless runner with its workspace facts, and an unknown id', () => {
    const sel = resolveSelection(GROUPS, 'rn_SOLO0001');
    expect(sel).toMatchObject({ kind: 'runner', ref: 'R-0001' });
    if (sel?.kind !== 'runner') throw new Error('expected a runner');
    expect(sel.facts.map((f) => f.label)).toEqual(['Workspace', 'Registered', 'Added']);
    expect(resolveSelection(GROUPS, 'hs_nope')).toEqual({ kind: 'not-found', id: 'hs_nope' });
  });

  it('lays out held chunks then FREE lines up to capacity', () => {
    const ws: WorkspaceRow = { workspaceId: 'ws_a', capacity: 3, inUse: 1, flag: null, environments: [] };
    const held = { chunkId: 'ch_1', shortRef: 'C-0001', step: 'build', runnerName: 'name-rn_1', environments: 1 };
    const groups: HostGroups<RunnerRow> = {
      cards: [{ card: card('hs_AAAA1111', { workspaces: [ws], heldChunks: new Map([['ws_a', [held]]]) }), rows: [] }],
      hostless: [],
    };
    const sel = resolveSelection(groups, 'hs_AAAA1111');
    if (sel?.kind !== 'host') throw new Error('expected a host');

    expect(sel.workspaces[0].lines.map((l) => l.kind)).toEqual(['held', 'free', 'free']);
  });
});

describe('firstFleetId', () => {
  it('prefers the first host, then the first hostless runner', () => {
    expect(firstFleetId(fleetList(GROUPS))).toBe('hs_AAAA1111');
    expect(firstFleetId(fleetList({ cards: [], hostless: GROUPS.hostless }))).toBe('rn_SOLO0001');
    expect(firstFleetId(fleetList({ cards: [], hostless: [] }))).toBeNull();
  });
});
