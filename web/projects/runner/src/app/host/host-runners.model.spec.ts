import type { runnerApi } from 'fleet';

import { brakeLabel, hostRunnerRows } from './host-runners.model';

describe('brakeLabel', () => {
  it('reads null while neither brake is set', () => {
    expect(brakeLabel(null)).toBeNull();
    expect(brakeLabel({ local: false, hub: false, effective: false })).toBeNull();
  });

  it('names which side set the brake', () => {
    expect(brakeLabel({ local: true, hub: false, effective: true })).toBe('paused');
    expect(brakeLabel({ local: false, hub: true, effective: true })).toBe('paused by hub');
    expect(brakeLabel({ local: true, hub: true, effective: true })).toBe('paused here and by hub');
  });

  it('carries the local brake reason', () => {
    expect(brakeLabel({ local: true, hub: false, effective: true, local_reason: 'usage limit' })).toBe('paused: usage limit');
  });
});

describe('hostRunnerRows', () => {
  const serving: runnerApi.HostRunnerView = {
    runner_id: 'rn_1',
    name: 'r-claude',
    state: 'serving',
    pause: { local: true, hub: false, effective: true },
    agents_in_use: 1,
    max_agents: 2,
    bindings: [{ project: 'blizzard', workspace_id: 'ws-a', agents: 2 }],
  };
  const halted: runnerApi.HostRunnerView = {
    runner_id: null,
    name: 'r-broken',
    state: 'halted',
    halted_reasons: ['no hub token'],
  };

  it('folds a serving runner with its brake, agents, bindings, and board link', () => {
    expect(hostRunnerRows([serving])).toEqual([
      {
        runnerId: 'rn_1',
        name: 'r-claude',
        serving: true,
        brake: 'paused',
        agents: '1/2 agents',
        bindings: ['blizzard · ws-a · 2 agents'],
        haltedReasons: [],
        boardLink: ['/runners', 'rn_1', 'board'],
      },
    ]);
  });

  it('folds a halted runner with its reasons and no board to open', () => {
    const [row] = hostRunnerRows([halted]);
    expect(row).toMatchObject({ runnerId: null, serving: false, brake: null, agents: null, boardLink: null });
    expect(row.haltedReasons).toEqual(['no hub token']);
  });
});
