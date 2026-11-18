import { describe, expect, it } from 'vitest';
import type { RunnerRegistryView } from 'fleet/shell';
import { servingProjectRunners } from './runners.domain';
import { toProjectRunner } from './runners.translate';

describe('project runner projection', () => {
  it('uses online and the hub claimability judgment, not paused flags', () => {
    const runner = toProjectRunner({ runner_id: 'r', runner_name: 'Runner', connection: 'connected', online: true,
      hub_paused: true, projects: [{ project_id: 'p', claimable: true }] } as unknown as RunnerRegistryView);
    expect(servingProjectRunners('p', [runner])).toEqual([runner]);
    expect(servingProjectRunners('other', [runner])).toEqual([]);
    expect(servingProjectRunners('p', [{ ...runner, online: false }])).toEqual([]);
    expect(servingProjectRunners('p', [{ ...runner, projects: [{ project_id: 'p', claimable: false }] }])).toEqual([]);
  });
});
