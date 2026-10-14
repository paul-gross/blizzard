import { type runnerApi } from 'fleet';
import { describe, expect, it } from 'vitest';

import { headerConnectionLabel, headerStatCells } from './app-header.model';

describe('headerConnectionLabel', () => {
  it('reads a reconnecting stream first, whatever else holds', () => {
    expect(headerConnectionLabel('reconnecting', true, true, true)).toBe('reconnecting…');
  });

  it('reads a settled stream auth failure as degraded, over the host read', () => {
    expect(headerConnectionLabel('closed', true, true, true)).toBe('degraded');
  });

  it("falls through to the host read's own state", () => {
    expect(headerConnectionLabel('open', false, true, false)).toBe('connecting…');
    expect(headerConnectionLabel('open', false, false, true)).toBe('offline');
    expect(headerConnectionLabel('open', false, false, false)).toBe('ok');
  });
});

describe('headerStatCells', () => {
  it('withholds every cell while the host read is pending', () => {
    expect(headerStatCells(true, undefined, undefined)).toEqual([]);
  });

  it('counts held environments over every workspace, and serving runners’ agents over their ceilings', () => {
    const runners: runnerApi.HostRunnerView[] = [
      { runner_id: 'rn_1', name: 'a', state: 'serving', agents_in_use: 2, max_agents: 4 },
      { runner_id: 'rn_2', name: 'b', state: 'serving', agents_in_use: 1, max_agents: 2 },
      { runner_id: null, name: 'c', state: 'halted', agents_in_use: 9, max_agents: 9 },
    ];
    const environments: runnerApi.HostWorkspaceEnvironmentsView[] = [
      { workspace_id: 'ws-a', items: [{ environment_id: 'e1', held: true, chunk_id: 'ch_1' }, { environment_id: 'e2' }] },
      { workspace_id: 'ws-b', items: [{ environment_id: 'e3', held: false }] },
    ];
    expect(headerStatCells(false, runners, environments)).toEqual([
      { key: 'envs', label: 'Envs', value: 1, capacity: 3 },
      { key: 'agents', label: 'Agents', value: 3, capacity: 6 },
    ]);
  });

  it('reads zeroes off resolved but empty reads', () => {
    expect(headerStatCells(false, undefined, undefined)).toEqual([
      { key: 'envs', label: 'Envs', value: 0, capacity: 0 },
      { key: 'agents', label: 'Agents', value: 0, capacity: 0 },
    ]);
  });
});
