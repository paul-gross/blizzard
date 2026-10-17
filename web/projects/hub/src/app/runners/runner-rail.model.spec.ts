import type { RunnerConnection } from 'fleet';

import type { HostCard, HostGroups } from './host-rows.model';
import { agentGauge, envGauge, railVm } from './runner-rail.model';
import type { RunnerRow } from './runner-rows.model';

const card = (id: string, workspaces: { capacity: number | null; inUse: number }[]): HostCard =>
  ({
    hostId: id,
    workspaces: workspaces.map((ws, i) => ({ workspaceId: `${id}-ws${i}`, flag: null, environments: [], ...ws })),
  }) as unknown as HostCard;

const row = (id: string, over: Partial<RunnerRow> = {}): RunnerRow =>
  ({
    runner_id: id,
    connection: 'online' as RunnerConnection,
    used: 0,
    env_capacity: null,
    bindingLines: [],
    ...over,
  }) as unknown as RunnerRow;

const binding = (agents: number, agentsInUse: number) => ({ project: 'p', workspaceId: 'w', agents, agentsInUse, unreported: false });

const groups = (cards: HostGroups<RunnerRow>['cards'], hostless: RunnerRow[] = []): HostGroups<RunnerRow> => ({ cards, hostless });

describe('envGauge', () => {
  it('sums every host workspace and each host-less pool', () => {
    const gauge = envGauge(
      groups([{ card: card('h1', [{ capacity: 3, inUse: 2 }, { capacity: 2, inUse: 0 }]), rows: [] }], [
        row('solo', { env_capacity: 4, used: 1 }),
      ]),
    );

    expect(gauge).toEqual({ used: 3, total: 9 });
  });

  it('counts a workspace or runner with null capacity toward neither number', () => {
    const gauge = envGauge(
      groups([{ card: card('h1', [{ capacity: null, inUse: 5 }, { capacity: 2, inUse: 1 }]), rows: [] }], [
        row('old', { env_capacity: null, used: 3 }),
      ]),
    );

    expect(gauge).toEqual({ used: 1, total: 2 });
  });

  it('does not count a hosted runner’s own capacity, which its host workspaces already carry', () => {
    const hosted = row('rn', { env_capacity: 8, used: 4 });

    expect(envGauge(groups([{ card: card('h1', [{ capacity: 2, inUse: 1 }]), rows: [hosted] }]))).toEqual({ used: 1, total: 2 });
  });

  it('is null when the total is zero', () => {
    expect(envGauge(groups([]))).toBeNull();
    expect(envGauge(groups([{ card: card('h1', [{ capacity: 0, inUse: 0 }]), rows: [] }]))).toBeNull();
  });
});

describe('agentGauge', () => {
  it('sums the binding lines’ agents in use over declared', () => {
    expect(agentGauge(row('r', { bindingLines: [binding(3, 1), binding(2, 2)] }))).toEqual({ used: 3, total: 5 });
  });

  it('is null for a runner declaring no bindings', () => {
    expect(agentGauge(row('r'))).toBeNull();
  });
});

describe('railVm', () => {
  it('lists hosted runners in card order, then the host-less, each with its agents', () => {
    const a = row('a', { bindingLines: [binding(1, 0)] });
    const b = row('b');
    const vm = railVm(groups([{ card: card('h1', [{ capacity: 1, inUse: 0 }]), rows: [a] }], [b]));

    expect(vm.rows.map((entry) => entry.row.runner_id)).toEqual(['a', 'b']);
    expect(vm.rows.map((entry) => entry.agents)).toEqual([{ used: 0, total: 1 }, null]);
    expect(vm.envs).toEqual({ used: 0, total: 1 });
  });
});
