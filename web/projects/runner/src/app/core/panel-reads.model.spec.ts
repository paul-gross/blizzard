import type { runnerApi } from 'fleet';

import {
  anyHubReachable,
  attributedProject,
  dashboardsByRunner,
  inProject,
  mergeLeases,
  ownByProject,
  type PanelLease,
  type PanelProject,
  projectCatalog,
  projectNameFor,
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

describe('attributedProject', () => {
  const projects = [project('blizzard', { entries: ['blizzard', 'proj_1'] }), project('winter')];

  it('resolves the lease’s wire project id to its project’s slug', () => {
    expect(attributedProject(lease({ project_id: 'proj_1' }), projects)).toBe('blizzard');
  });

  it('keeps the wire id when no project resolves it', () => {
    expect(attributedProject(lease({ project_id: 'proj_9' }), projects)).toBe('proj_9');
  });

  it('gives null for a lease naming no project, even on a runner of one project', () => {
    expect(attributedProject(lease({ project_id: null }), [project('winter')])).toBeNull();
    expect(attributedProject(lease(), [project('winter')])).toBeNull();
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

describe('ownByProject', () => {
  it('tags each item with its runner and the project its own wire project id names, null where it names none', () => {
    const projects = [project('blizzard', { entries: ['blizzard', 'proj_1'] })];
    const owned = ownByProject(
      [
        { id: 'a', chunk_id: 'ch_1', project_id: 'proj_1' },
        { id: 'b', chunk_id: 'ch_2', project_id: 'proj_9' },
        { id: 'c', chunk_id: 'ch_3', project_id: null },
        { id: 'd' },
      ],
      R1,
      projects,
    );

    expect(owned.map((item) => [item.id, item.owner, item.projectSlug])).toEqual([
      ['a', R1, 'blizzard'],
      ['b', R1, 'proj_9'],
      ['c', R1, null],
      ['d', R1, null],
    ]);
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
