import { describe, expect, it } from 'vitest';
import { runnerRegistryView } from 'fleet/testing';
import { servingProjectRunners } from './runners.domain';
import { toProjectRunner } from './runners.translate';

describe('project runner projection', () => {
  it('uses online and the hub claimability judgment, not paused flags', () => {
    const runner = toProjectRunner(runnerRegistryView({ runner_id: 'r', runner_name: 'Runner', connection: 'online', online: true,
      hub_paused: true, projects: [{ entry: 'p', project_id: 'p', claimable: true, name: 'Project', slug: 'project', color: 'teal' }] }));
    expect(servingProjectRunners('p', [runner])).toEqual([runner]);
    expect(servingProjectRunners('other', [runner])).toEqual([]);
    expect(servingProjectRunners('p', [{ ...runner, online: false }])).toEqual([]);
    expect(servingProjectRunners('p', [{ ...runner, projects: [{ project_id: 'p', claimable: false }] }])).toEqual([]);
  });
});
