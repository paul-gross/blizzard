import { projectColorValue, type runnerApi } from 'fleet';

import {
  anyHubReachable,
  chunkProjectLookup,
  dashboardsByRunner,
  environmentsById,
  inProject,
  leaseProject,
  mergeLeases,
  ownByChunk,
  type PanelLease,
  type PanelProject,
  projectCatalog,
  projectNameFor,
  projectTint,
  type RunnerDashboard,
  type RunnerOwner,
  runnerProjects,
  scopedOwners,
  servesProject,
  servingOwners,
  slugForEntry,
} from './panel-reads.model';

const R1: RunnerOwner = { runnerId: 'rn_1', name: 'r1', label: 'r1' };
const R2: RunnerOwner = { runnerId: 'rn_2', name: 'r2', label: 'r2' };

function hostRunner(overrides: Partial<runnerApi.HostRunnerView> = {}): runnerApi.HostRunnerView {
  return {
    runner_id: 'rn_1',
    name: 'r1',
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: false, hub: false, effective: false, local_reason: null },
    bindings: [],
    agents_in_use: 0,
    max_agents: 4,
    ...overrides,
  };
}

function binding(project: string): runnerApi.HostRunnerBindingView {
  return { project, workspace_id: 'ws', agents: 4 };
}

/** A dashboard carrying only `runner.served_projects` and `runner.hub` — what the project and hub folds read. */
function dashboard(
  served: readonly Partial<runnerApi.ServedProjectStatusView>[] = [],
  reachable = true,
): runnerApi.DashboardView {
  return {
    runner: { served_projects: served.map((s) => ({ resolved: true, entry: '', ...s })), hub: { reachable } },
  } as unknown as runnerApi.DashboardView;
}

function project(slug: string, overrides: Partial<PanelProject> = {}): PanelProject {
  return { slug, name: slug, entries: [slug], color: null, ...overrides };
}

function lease(overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView {
  return {
    lease_id: 'L-1',
    chunk_id: 'ch_1',
    graph_id: 'gr_1',
    node_id: 'nd_1',
    node_name: 'build',
    epoch: 1,
    session_id: null,
    pid: null,
    environment_id: null,
    workdir: null,
    created_at: '2026-07-16T11:00:00+00:00',
    closed_at: null,
    closure_healthy: true,
    closure_reason: null,
    display_status: 'running',
    last_heartbeat_at: null,
    stale_after_seconds: 3600,
    state: 'running',
    ...overrides,
  } as runnerApi.LeaseView;
}

function panelLease(overrides: Partial<runnerApi.LeaseView>, owner: RunnerOwner, projectSlug: string | null): PanelLease {
  return { ...lease(overrides), owner, projectSlug };
}

function env(overrides: Partial<runnerApi.HostEnvironmentView> & { environment_id: string }): runnerApi.HostEnvironmentView {
  return { ...overrides };
}

describe('servingOwners', () => {
  it('keeps each serving runner with an id, in wire order, labeled by its bare name', () => {
    const owners = servingOwners([
      hostRunner({ runner_id: 'rn_2', name: 'r2' }),
      hostRunner({ runner_id: 'rn_3', name: 'r3', state: 'halted', served: false }),
      hostRunner({ runner_id: null, name: 'pending' }),
      hostRunner({ runner_id: 'rn_1', name: 'r1' }),
    ]);

    expect(owners).toEqual([
      { runnerId: 'rn_2', name: 'r2', label: 'r2' },
      { runnerId: 'rn_1', name: 'r1', label: 'r1' },
    ]);
  });
});

describe('scopedOwners', () => {
  it('keeps every runner for All', () => {
    expect(scopedOwners([R1, R2], null)).toEqual([R1, R2]);
  });

  it('narrows to the runner the filter names, none for an unknown name', () => {
    expect(scopedOwners([R1, R2], 'r2')).toEqual([R2]);
    expect(scopedOwners([R1, R2], 'gone')).toEqual([]);
  });
});

describe('runnerProjects', () => {
  it('names each served project by its slug, gathering its entry and id, carrying its colour', () => {
    const projects = runnerProjects(
      dashboard([{ slug: 'blizzard', name: 'Blizzard', entry: 'blizzard-entry', project_id: 'proj_1', color: 'teal' }]),
      hostRunner({ bindings: [binding('blizzard-entry')] }),
    );

    expect(projects).toEqual([{ slug: 'blizzard', name: 'Blizzard', entries: ['blizzard-entry', 'proj_1'], color: 'teal' }]);
  });

  it('falls back to the slug for a nameless project and to no colour for an uncoloured one', () => {
    expect(runnerProjects(dashboard([{ slug: 'ops', entry: 'ops' }]), undefined)).toEqual([
      { slug: 'ops', name: 'ops', entries: ['ops'], color: null },
    ]);
  });

  it('skips a served project the hub has not resolved to a slug', () => {
    expect(runnerProjects(dashboard([{ slug: null, entry: 'unknown' }]), undefined)).toEqual([]);
  });

  it('adds each binding no served project accounts for under its own entry, uncoloured', () => {
    const projects = runnerProjects(
      dashboard([{ slug: 'blizzard', entry: 'blizzard', color: 'teal' }]),
      hostRunner({ bindings: [binding('blizzard'), binding('winter')] }),
    );

    expect(projects.map((p) => [p.slug, p.color])).toEqual([
      ['blizzard', 'teal'],
      ['winter', null],
    ]);
    expect(projects[1]).toEqual({ slug: 'winter', name: 'winter', entries: ['winter'], color: null });
  });

  it('reads the bindings alone before the dashboard lands', () => {
    expect(runnerProjects(undefined, hostRunner({ bindings: [binding('p')] }))).toEqual([project('p')]);
  });
});

describe('projectCatalog', () => {
  it('folds every runner’s projects to one per slug, entries unioned, sorted by name', () => {
    const catalog = projectCatalog(
      new Map([
        ['rn_1', [project('winter', { name: 'Winter', entries: ['winter'] }), project('blizzard', { name: 'Blizzard', entries: ['bz'], color: 'teal' })]],
        ['rn_2', [project('blizzard', { name: 'Blizzard', entries: ['bz', 'proj_1'] })]],
      ]),
    );

    expect(catalog.map((p) => p.slug)).toEqual(['blizzard', 'winter']);
    expect(catalog[0]).toEqual({ slug: 'blizzard', name: 'Blizzard', entries: ['bz', 'proj_1'], color: 'teal' });
  });
});

describe('slugForEntry', () => {
  const projects = [project('blizzard', { entries: ['bz', 'proj_1'] })];

  it('resolves a slug, a binding entry, or a project id to the slug', () => {
    expect(slugForEntry(projects, 'blizzard')).toBe('blizzard');
    expect(slugForEntry(projects, 'bz')).toBe('blizzard');
    expect(slugForEntry(projects, 'proj_1')).toBe('blizzard');
  });

  it('gives null for an entry no project names', () => {
    expect(slugForEntry(projects, 'proj_9')).toBeNull();
  });
});

describe('servesProject', () => {
  it('is always true for All, else whether a project has the slug', () => {
    expect(servesProject([], null)).toBe(true);
    expect(servesProject([project('p')], 'p')).toBe(true);
    expect(servesProject([project('p')], 'q')).toBe(false);
  });
});

describe('leaseProject', () => {
  const projects = [project('blizzard', { entries: ['blizzard', 'proj_1'] }), project('winter')];

  it('tells the project from the environment while it still holds the lease’s own chunk', () => {
    const envs = environmentsById([{ workspace_id: 'ws', items: [env({ environment_id: 'e1', chunk_id: 'ch_1', project: 'proj_1' })] }]);
    expect(leaseProject(lease({ environment_id: 'e1' }), projects, envs)).toBe('blizzard');
  });

  it('keeps the environment’s own entry when no project resolves it', () => {
    const envs = environmentsById([{ workspace_id: 'ws', items: [env({ environment_id: 'e1', chunk_id: 'ch_1', project: 'proj_9' })] }]);
    expect(leaseProject(lease({ environment_id: 'e1' }), projects, envs)).toBe('proj_9');
  });

  it('ignores an environment since taken by another chunk, falling to the runner’s sole project', () => {
    const envs = environmentsById([{ workspace_id: 'ws', items: [env({ environment_id: 'e1', chunk_id: 'ch_2', project: 'proj_1' })] }]);
    expect(leaseProject(lease({ environment_id: 'e1' }), [project('winter')], envs)).toBe('winter');
    expect(leaseProject(lease({ environment_id: 'e1' }), projects, envs)).toBeNull();
  });

  it('gives null for an environment-less lease on a runner of several projects', () => {
    expect(leaseProject(lease(), projects, new Map())).toBeNull();
  });
});

describe('environmentsById', () => {
  it('indexes every workspace’s environments by id, tolerating a workspace with no items', () => {
    const e1 = env({ environment_id: 'e1' });
    const e2 = env({ environment_id: 'e2' });
    const byId = environmentsById([
      { workspace_id: 'a', items: [e1] },
      { workspace_id: 'b' },
      { workspace_id: 'c', items: [e2] },
    ]);

    expect([...byId.keys()]).toEqual(['e1', 'e2']);
    expect(byId.get('e2')).toBe(e2);
  });
});

describe('mergeLeases', () => {
  it('puts live leases first, then newest first, across runners', () => {
    const merged = mergeLeases([
      [
        panelLease({ lease_id: 'old-closed', state: 'closed', created_at: '2026-07-16T09:00:00+00:00' }, R1, null),
        panelLease({ lease_id: 'old-live', created_at: '2026-07-16T10:00:00+00:00' }, R1, null),
      ],
      [
        panelLease({ lease_id: 'new-closed', state: 'closed', created_at: '2026-07-16T12:00:00+00:00' }, R2, null),
        panelLease({ lease_id: 'new-live', state: 'stale', created_at: '2026-07-16T11:00:00+00:00' }, R2, null),
      ],
    ]);

    expect(merged.map((l) => l.lease_id)).toEqual(['new-live', 'old-live', 'new-closed', 'old-closed']);
  });
});

describe('chunkProjectLookup', () => {
  it('answers a chunk’s project per runner from the first lease that attributes it', () => {
    const lookup = chunkProjectLookup([
      panelLease({ chunk_id: 'ch_1' }, R1, null),
      panelLease({ chunk_id: 'ch_1' }, R1, 'blizzard'),
      panelLease({ chunk_id: 'ch_1' }, R1, 'winter'),
      panelLease({ chunk_id: 'ch_1' }, R2, 'ops'),
    ]);

    expect(lookup('rn_1', 'ch_1')).toBe('blizzard');
    expect(lookup('rn_2', 'ch_1')).toBe('ops');
  });

  it('gives null for an unknown chunk, a chunk on another runner, and no chunk', () => {
    const lookup = chunkProjectLookup([panelLease({ chunk_id: 'ch_1' }, R1, 'blizzard')]);

    expect(lookup('rn_1', 'ch_9')).toBeNull();
    expect(lookup('rn_2', 'ch_1')).toBeNull();
    expect(lookup('rn_1', null)).toBeNull();
  });
});

describe('ownByChunk', () => {
  it('tags each item with its runner and its chunk’s project, else the runner’s sole project', () => {
    const lookup = chunkProjectLookup([panelLease({ chunk_id: 'ch_1' }, R1, 'blizzard')]);
    const owned = ownByChunk([{ id: 'a', chunk_id: 'ch_1' }, { id: 'b', chunk_id: 'ch_2' }, { id: 'c' }], R1, [project('winter')], lookup);

    expect(owned).toEqual([
      { id: 'a', chunk_id: 'ch_1', owner: R1, projectSlug: 'blizzard' },
      { id: 'b', chunk_id: 'ch_2', owner: R1, projectSlug: 'winter' },
      { id: 'c', owner: R1, projectSlug: 'winter' },
    ]);
  });

  it('leaves an unattributable item on a runner of several projects with no project', () => {
    const owned = ownByChunk([{ chunk_id: 'ch_2' }], R1, [project('a'), project('b')], () => null);
    expect(owned[0].projectSlug).toBeNull();
  });
});

describe('inProject', () => {
  const items = [{ projectSlug: 'a' }, { projectSlug: null }, { projectSlug: 'b' }];

  it('keeps every item for All, including unattributed ones', () => {
    expect(inProject(items, null)).toEqual(items);
  });

  it('narrows to the slug, dropping unattributed items', () => {
    expect(inProject(items, 'a')).toEqual([{ projectSlug: 'a' }]);
  });
});

describe('projectTint', () => {
  it('paints a named colour through its theme value, and a hex as-is', () => {
    expect(projectTint('teal')).toBe(projectColorValue('teal'));
    expect(projectTint('#AABBCC')).toBe('#aabbcc');
  });

  it('gives null for no colour', () => {
    expect(projectTint(null)).toBeNull();
    expect(projectTint('')).toBeNull();
  });
});

describe('projectNameFor', () => {
  const projects = [project('bz', { name: 'Blizzard' })];

  it('names a known slug by its project, an unknown one by itself, and no project as null', () => {
    expect(projectNameFor(projects, 'bz')).toBe('Blizzard');
    expect(projectNameFor(projects, 'other')).toBe('other');
    expect(projectNameFor(projects, null)).toBeNull();
  });
});

describe('dashboardsByRunner', () => {
  it('indexes each dashboard by its runner id', () => {
    const d1 = dashboard();
    const d2 = dashboard();
    const byRunner = dashboardsByRunner([
      { owner: R1, dashboard: d1 },
      { owner: R2, dashboard: d2 },
    ]);

    expect(byRunner.get('rn_1')).toBe(d1);
    expect(byRunner.get('rn_2')).toBe(d2);
  });
});

describe('anyHubReachable', () => {
  it('is true once any runner reaches its hub', () => {
    expect(
      anyHubReachable([
        { owner: R1, dashboard: dashboard([], false) },
        { owner: R2, dashboard: dashboard([], true) },
      ]),
    ).toBe(true);
  });

  it('is false when none does, and for no dashboards', () => {
    expect(anyHubReachable([{ owner: R1, dashboard: dashboard([], false) }])).toBe(false);
    expect(anyHubReachable([])).toBe(false);
  });

  it('reads a malformed body — no runner, or no hub — as not reaching it', () => {
    const malformed: RunnerDashboard[] = [
      { owner: R1, dashboard: {} as runnerApi.DashboardView },
      { owner: R2, dashboard: { runner: {} } as unknown as runnerApi.DashboardView },
    ];
    expect(anyHubReachable(malformed)).toBe(false);
  });
});
