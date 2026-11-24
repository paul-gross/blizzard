import { hubRunner } from './runners.builders';
import {
  fleetIdOf,
  isUnassigned,
  runnerServes,
  servingProjectRunners,
  unresolvedEntries,
  vocabularyWord,
} from './runners.domain';
import type { HubRunner } from './runners.domain';

function runner(id: string, fields: Partial<HubRunner>): HubRunner {
  return hubRunner({ runner_id: id, ...fields });
}

describe('fleetIdOf', () => {
  const hosts = [{ host_id: 'hs_1', runner_ids: ['rn_1', 'rn_2'] }, { host_id: 'hs_2' }];

  it('names the host that lists the runner', () => {
    expect(fleetIdOf('rn_2', hosts)).toBe('hs_1');
  });

  it('names a runner no host lists, or any runner when there are no hosts, by itself', () => {
    expect(fleetIdOf('rn_9', hosts)).toBe('rn_9');
    expect(fleetIdOf('rn_1', [])).toBe('rn_1');
  });
});

describe('which runners serve a project', () => {
  const blizzard = {
    entry: 'blizzard',
    project_id: 'proj_b',
    slug: 'blizzard',
    name: 'Blizzard',
    claimable: true,
    color: null,
  };
  const celestial = {
    entry: 'proj_c',
    project_id: 'proj_c',
    slug: 'celestial',
    name: 'Celestial',
    claimable: true,
    color: null,
  };
  const unresolved = {
    entry: 'gone',
    project_id: null,
    slug: null,
    name: null,
    claimable: false,
    color: null,
  };

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
      runner('retired', {
        projects: [{ ...blizzard, claimable: false }],
        retired: true,
      }),
      runner('paused', {
        projects: [{ ...blizzard, claimable: false }],
        hub_paused: true,
      }),
      runner('elsewhere', { projects: [celestial] }),
      runner('unassigned', { projects: [] }),
    ];

    expect(servingProjectRunners('proj_b', runners).map((r) => r.runner_id)).toEqual([
      'live',
    ]);
    expect(servingProjectRunners('proj_x', runners)).toEqual([]);
  });
});

describe('vocabularyWord', () => {
  it('reads a known value as itself and an unknown one as its raw word', () => {
    expect(vocabularyWord('online')).toBe('online');
    expect(vocabularyWord({ kind: 'unknown', raw: 'dormant' })).toBe('dormant');
  });
});
