import { compactRef, type runnerApi } from 'fleet';

import { hostPanelPools, hostPoolsFor, runnerFocus, runnerNameResolver } from './host-pools.model';
import type { PanelProject } from './panel-reads.model';

const CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

function hostRunner(runnerId: string | null, name: string): runnerApi.HostRunnerView {
  return {
    runner_id: runnerId,
    name,
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: false, hub: false, effective: false, local_reason: null },
    bindings: [],
    agents_in_use: 0,
    max_agents: 4,
  };
}

/** One host environment, free unless it names a chunk — held agrees with chunk_id unless a test overrides it. */
function hostEnv(fields: Partial<runnerApi.HostEnvironmentView> & { environment_id: string }): runnerApi.HostEnvironmentView {
  const chunkId = fields.chunk_id ?? null;
  return { chunk_id: chunkId, held: chunkId !== null, held_since: null, project: null, runner_id: null, ...fields };
}

const names = (runnerId: string) => ({ rn_1: 'r1', rn_2: 'r2' })[runnerId] ?? runnerId;
const same = (entry: string) => entry;

describe('runnerNameResolver', () => {
  it('names a runner id off the host’s runner list, the id itself for one it does not list', () => {
    const name = runnerNameResolver([hostRunner('rn_1', 'r1'), hostRunner(null, 'pending')]);

    expect(name('rn_1')).toBe('r1');
    expect(name('rn_9')).toBe('rn_9');
  });
});

describe('hostPanelPools', () => {
  it('lays a workspace’s environments out as positional slots, in numeric environment order', () => {
    const [pool] = hostPanelPools(
      [{ workspace_id: 'ws', max_environments: 3 }],
      [
        {
          workspace_id: 'ws',
          items: [
            hostEnv({ environment_id: 'e10' }),
            hostEnv({ environment_id: 'e2', chunk_id: CHUNK, runner_id: 'rn_1', project: 'blizzard' }),
            hostEnv({ environment_id: 'e1' }),
          ],
        },
      ],
      names,
      same,
      null,
    );

    expect(pool.envs.map((env) => env.environmentId)).toEqual(['e1', 'e2', 'e10']);
    expect(pool.slots.map((slot) => slot.on)).toEqual([false, true, false]);
    expect(pool.capacity).toBe(3);
    expect(pool.inUse).toBe(1);
  });

  it('describes a held environment by its chunk, project, and runner, and a free one as free', () => {
    const [pool] = hostPanelPools(
      [{ workspace_id: 'ws', max_environments: 2 }],
      [
        {
          workspace_id: 'ws',
          items: [
            hostEnv({ environment_id: 'e1', chunk_id: CHUNK, runner_id: 'rn_1', project: 'proj_1' }),
            hostEnv({ environment_id: 'e2', held: false }),
          ],
        },
      ],
      names,
      (entry) => (entry === 'proj_1' ? 'blizzard' : entry),
      null,
    );

    expect(pool.envs[0]).toEqual({
      environmentId: 'e1',
      held: true,
      chunkRef: compactRef(CHUNK),
      chunkId: CHUNK,
      project: 'blizzard',
      holder: 'r1',
      elsewhere: false,
    });
    expect(pool.slots.map((slot) => slot.title)).toEqual([`e1 · ${compactRef(CHUNK)} · blizzard · r1`, 'e2 · free']);
  });

  it('leaves out of a held slot’s title whatever the host does not name', () => {
    const [pool] = hostPanelPools(
      [{ workspace_id: 'ws', max_environments: 1 }],
      [{ workspace_id: 'ws', items: [hostEnv({ environment_id: 'e1', held: true, project: '' })] }],
      names,
      same,
      null,
    );

    expect(pool.envs[0]).toMatchObject({ held: true, chunkRef: null, project: null, holder: null });
    expect(pool.slots[0].title).toBe('e1');
  });

  it('dims under a focus every held slot another runner — or no runner — holds, never a free one', () => {
    const [pool] = hostPanelPools(
      [{ workspace_id: 'ws', max_environments: 4 }],
      [
        {
          workspace_id: 'ws',
          items: [
            hostEnv({ environment_id: 'e1', chunk_id: CHUNK, runner_id: 'rn_1' }),
            hostEnv({ environment_id: 'e2', chunk_id: CHUNK, runner_id: 'rn_2' }),
            hostEnv({ environment_id: 'e3', chunk_id: CHUNK }),
            hostEnv({ environment_id: 'e4' }),
          ],
        },
      ],
      names,
      same,
      new Set(['rn_1']),
    );

    expect(pool.envs.map((env) => env.elsewhere)).toEqual([false, true, true, false]);
    expect(pool.slots.map((slot) => slot.dim)).toEqual([false, true, true, false]);
  });

  it('dims nothing without a focus', () => {
    const [pool] = hostPanelPools(
      [{ workspace_id: 'ws', max_environments: 1 }],
      [{ workspace_id: 'ws', items: [hostEnv({ environment_id: 'e1', chunk_id: CHUNK, runner_id: 'rn_2' })] }],
      names,
      same,
      null,
    );

    expect(pool.slots[0].dim).toBe(false);
  });

  it('lists declared workspaces in declaration order, then undeclared ones the host reports, with no capacity', () => {
    const pools = hostPanelPools(
      [
        { workspace_id: 'web', max_environments: 2 },
        { workspace_id: 'local', max_environments: 6 },
      ],
      [
        { workspace_id: 'stray', items: [hostEnv({ environment_id: 'e1', chunk_id: CHUNK, runner_id: 'rn_1' })] },
        { workspace_id: 'local', items: [hostEnv({ environment_id: 'e1' })] },
      ],
      names,
      same,
      null,
    );

    expect(pools.map((pool) => [pool.workspaceId, pool.capacity, pool.inUse])).toEqual([
      ['web', 2, 0],
      ['local', 6, 0],
      ['stray', null, 1],
    ]);
    expect(pools[0].envs).toEqual([]);
  });
});

describe('runnerFocus', () => {
  it('is null — nothing dims — while the Runner filter is All', () => {
    expect(runnerFocus(null, [{ id: 'rn_1' }], (item) => item.id)).toBeNull();
  });

  it('collects the ids of the items the filter leaves, skipping id-less ones', () => {
    const focus = runnerFocus('r1', [{ id: 'rn_1' }, { id: null }], (item) => item.id);
    expect(focus).toEqual(new Set(['rn_1']));
  });
});

describe('hostPoolsFor', () => {
  it('names holders off the runner list and resolves project entries to the slugs the filters use', () => {
    const projects: PanelProject[] = [{ slug: 'blizzard', name: 'Blizzard', entries: ['blizzard', 'proj_1'], color: null }];
    const [pool] = hostPoolsFor(
      {
        workspaces: [{ workspace_id: 'ws', max_environments: 2 }],
        groups: [
          {
            workspace_id: 'ws',
            items: [
              hostEnv({ environment_id: 'e1', chunk_id: CHUNK, runner_id: 'rn_1', project: 'proj_1' }),
              hostEnv({ environment_id: 'e2', chunk_id: CHUNK, runner_id: 'rn_9', project: 'proj_9' }),
            ],
          },
        ],
        runners: [hostRunner('rn_1', 'r1')],
        projects,
      },
      new Set(['rn_1']),
    );

    expect(pool.envs.map((env) => [env.project, env.holder, env.elsewhere])).toEqual([
      ['blizzard', 'r1', false],
      ['proj_9', 'rn_9', true],
    ]);
  });
});
