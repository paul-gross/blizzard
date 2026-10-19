import type { HostPanelEnv, HostPanelPool } from '../core/host-pools.model';
import type { PanelProject } from '../core/panel-reads.model';
import { hostEnvGroups } from './host-environments.model';

const ENV = (overrides: Partial<HostPanelEnv>): HostPanelEnv => ({
  environmentId: 'e1',
  held: false,
  chunkRef: null,
  chunkId: null,
  project: null,
  holder: null,
  elsewhere: false,
  ...overrides,
});

const POOL = (envs: HostPanelEnv[]): HostPanelPool => ({ workspaceId: 'ws', capacity: 3, inUse: 2, envs, slots: [] });

const PROJECTS: PanelProject[] = [{ slug: 'console', name: 'Console', entries: ['console'], color: 'amber' }];

describe('hostEnvGroups', () => {
  it('resolves each held environment’s project by slug to its name and colour', () => {
    const [group] = hostEnvGroups([POOL([ENV({ held: true, chunkId: 'ch_1', project: 'console', holder: 'r1' })])], PROJECTS);

    expect(group).toMatchObject({ workspaceId: 'ws', capacity: 3, inUse: 2 });
    expect(group.envs[0]).toMatchObject({ project: 'console', projectName: 'Console', color: 'amber', holder: 'r1' });
  });

  it('falls back to the slug, uncoloured, for a project no served one names', () => {
    const [group] = hostEnvGroups([POOL([ENV({ held: true, project: 'ops' })])], PROJECTS);

    expect(group.envs[0]).toMatchObject({ projectName: 'ops', color: null });
  });

  it('leaves a free environment without a project', () => {
    const [group] = hostEnvGroups([POOL([ENV({})])], PROJECTS);

    expect(group.envs[0]).toMatchObject({ held: false, projectName: null, color: null });
  });
});
