import { describe, expect, it } from 'vitest';

import { hostEnvironment, workspaceEnvironments } from '../../core/host-environment.builders';
import type { WorkspaceEnvironments } from '../../core/host-environment.domain';
import { hostRunner } from '../../core/host-runner.builders';
import type { HostRunner } from '../../core/host-runner.domain';
import { headerConnectionLabel, headerStatCells } from './app-header.model';

/** One host runner, served whenever it has an id, with no pause. */
function runner(fields: Pick<HostRunner, 'runner_id' | 'name' | 'state' | 'agents_in_use' | 'max_agents'>): HostRunner {
  return hostRunner({ ...fields, served: fields.runner_id !== null });
}

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
  it('withholds every cell while either of the host’s reads is pending', () => {
    expect(headerStatCells(true, undefined, undefined)).toEqual([]);
  });

  it('counts held environments over every workspace, and serving runners’ agents over their ceilings', () => {
    const runners: HostRunner[] = [
      runner({ runner_id: 'rn_1', name: 'a', state: 'serving', agents_in_use: 2, max_agents: 4 }),
      runner({ runner_id: 'rn_2', name: 'b', state: 'serving', agents_in_use: 1, max_agents: 2 }),
      runner({ runner_id: null, name: 'c', state: 'halted', agents_in_use: 9, max_agents: 9 }),
    ];
    const environments: WorkspaceEnvironments[] = [
      workspaceEnvironments([hostEnvironment({ held: true }), hostEnvironment()]),
      workspaceEnvironments([hostEnvironment()]),
    ];
    expect(headerStatCells(false, runners, environments)).toEqual([
      { key: 'envs', label: 'Envs', value: 1, capacity: 3 },
      { key: 'agents', label: 'Agents', value: 3, capacity: 6 },
    ]);
  });

  it('counts an environment by its held flag alone', () => {
    const environments: WorkspaceEnvironments[] = [workspaceEnvironments([hostEnvironment({ held: false })])];
    expect(headerStatCells(false, [], environments)).toEqual([
      { key: 'envs', label: 'Envs', value: 0, capacity: 1 },
      { key: 'agents', label: 'Agents', value: 0, capacity: 0 },
    ]);
  });

  it('narrows to one runner by name: the environments it holds over every environment, and its own agents', () => {
    const runners: HostRunner[] = [
      runner({ runner_id: 'rn_1', name: 'a', state: 'serving', agents_in_use: 2, max_agents: 4 }),
      runner({ runner_id: 'rn_2', name: 'b', state: 'serving', agents_in_use: 1, max_agents: 2 }),
    ];
    const environments: WorkspaceEnvironments[] = [
      workspaceEnvironments([
        hostEnvironment({ held: true, runner_id: 'rn_1' }),
        hostEnvironment({ held: true, runner_id: 'rn_2' }),
        hostEnvironment(),
      ]),
    ];
    expect(headerStatCells(false, runners, environments, 'b')).toEqual([
      { key: 'envs', label: 'Envs', value: 1, capacity: 3 },
      { key: 'agents', label: 'Agents', value: 1, capacity: 2 },
    ]);
  });

  it('reads zeroes off resolved but empty reads', () => {
    expect(headerStatCells(false, undefined, undefined)).toEqual([
      { key: 'envs', label: 'Envs', value: 0, capacity: 0 },
      { key: 'agents', label: 'Agents', value: 0, capacity: 0 },
    ]);
  });
});
