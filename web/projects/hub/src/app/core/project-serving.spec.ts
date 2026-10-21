import type { RunnerRegistryView } from 'fleet';

import { isUnassigned, runnerServes, servingRunners, unresolvedEntries } from './project-serving';

function runner(id: string, fields: Partial<RunnerRegistryView>): RunnerRegistryView {
  return { runner_id: id, name: id, online: true, hub_paused: false, ...fields } as RunnerRegistryView;
}

describe('which runners serve a project', () => {
  const blizzard = { entry: 'blizzard', project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard', claimable: true };
  const celestial = { entry: 'proj_c', project_id: 'proj_c', slug: 'celestial', name: 'Celestial', claimable: true };
  const unresolved = { entry: 'gone', project_id: null, slug: null, name: null, claimable: false };

  it('reads a runner’s declaration: what it serves, whether it serves nothing, and what resolved to no project', () => {
    const both = runner('r1', { projects: [blizzard, celestial, unresolved] });

    expect(runnerServes(both, 'proj_b')).toBe(true);
    expect(runnerServes(both, 'proj_x')).toBe(false);
    expect(isUnassigned(both)).toBe(false);
    expect(unresolvedEntries(both)).toEqual(['gone']);
    expect(isUnassigned(runner('r2', { projects: [] }))).toBe(true);
    expect(isUnassigned(runner('r3', {}))).toBe(true);
  });

  it('counts only live runners the hub judges can claim the project', () => {
    const runners = [
      runner('live', { projects: [blizzard] }),
      runner('offline', { projects: [blizzard], online: false }),
      runner('retired', { projects: [{ ...blizzard, claimable: false }], retired: true }),
      runner('paused', { projects: [{ ...blizzard, claimable: false }], hub_paused: true }),
      runner('elsewhere', { projects: [celestial] }),
      runner('unassigned', { projects: [] }),
    ];

    expect(servingRunners('proj_b', runners).map((r) => r.runner_id)).toEqual(['live']);
    expect(servingRunners('proj_x', runners)).toEqual([]);
  });
});
