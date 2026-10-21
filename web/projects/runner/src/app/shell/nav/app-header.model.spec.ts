import { type runnerApi } from 'fleet';
import { describe, expect, it } from 'vitest';

import { headerConnectionLabel, headerStatCells } from './app-header.model';

/** One host environment, free unless it names a chunk — held agrees with chunk_id unless a test overrides it. */
function hostEnv(fields: Partial<runnerApi.HostEnvironmentView> & { environment_id: string }): runnerApi.HostEnvironmentView {
  const chunkId = fields.chunk_id ?? null;
  return { chunk_id: chunkId, held: chunkId !== null, held_since: null, project: null, runner_id: null, ...fields };
}

/** One host runner, served whenever it has an id, with no bindings, halt reasons, or pause. */
function hostRunner(
  fields: Pick<runnerApi.HostRunnerView, 'runner_id' | 'name' | 'state' | 'agents_in_use' | 'max_agents'>,
): runnerApi.HostRunnerView {
  return { ...fields, served: fields.runner_id !== null, halted_reasons: [], pause: null, bindings: [] };
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
    const runners: runnerApi.HostRunnerView[] = [
      hostRunner({ runner_id: 'rn_1', name: 'a', state: 'serving', agents_in_use: 2, max_agents: 4 }),
      hostRunner({ runner_id: 'rn_2', name: 'b', state: 'serving', agents_in_use: 1, max_agents: 2 }),
      hostRunner({ runner_id: null, name: 'c', state: 'halted', agents_in_use: 9, max_agents: 9 }),
    ];
    const environments: runnerApi.HostWorkspaceEnvironmentsView[] = [
      { workspace_id: 'ws-a', items: [hostEnv({ environment_id: 'e1', held: true, chunk_id: 'ch_1' }), hostEnv({ environment_id: 'e2' })] },
      { workspace_id: 'ws-b', items: [hostEnv({ environment_id: 'e3', held: false })] },
    ];
    expect(headerStatCells(false, runners, environments)).toEqual([
      { key: 'envs', label: 'Envs', value: 1, capacity: 3 },
      { key: 'agents', label: 'Agents', value: 3, capacity: 6 },
    ]);
  });

  it('narrows to one runner by name: the environments it holds over every environment, and its own agents', () => {
    const runners: runnerApi.HostRunnerView[] = [
      hostRunner({ runner_id: 'rn_1', name: 'a', state: 'serving', agents_in_use: 2, max_agents: 4 }),
      hostRunner({ runner_id: 'rn_2', name: 'b', state: 'serving', agents_in_use: 1, max_agents: 2 }),
    ];
    const environments: runnerApi.HostWorkspaceEnvironmentsView[] = [
      {
        workspace_id: 'ws-a',
        items: [
          hostEnv({ environment_id: 'e1', held: true, chunk_id: 'ch_1', runner_id: 'rn_1' }),
          hostEnv({ environment_id: 'e2', held: true, chunk_id: 'ch_2', runner_id: 'rn_2' }),
          hostEnv({ environment_id: 'e3' }),
        ],
      },
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
