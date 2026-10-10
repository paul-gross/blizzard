import type { ConfigChangeView, ProjectView, RepositorySummary, RunnerRegistryView, WorkSourceSummary } from 'fleet';

import {
  failureFor,
  formerSlugs,
  lensedListState,
  linkChoices,
  linkedNames,
  missingWiring,
  projectPatch,
  projectRows,
  repositoryLinkRows,
  servedProjectIds,
  sourceLinkRows,
  wiringCallout,
} from './projects.model';

function project(id: string, slug: string, name: string): ProjectView {
  return { project_id: id, slug, name, description: '', revision: 1, created_at: '2026-10-01T00:00:00Z' };
}

const BLIZZARD = project('proj_b', 'blizzard', 'Blizzard');
const WINTER = project('proj_w', 'winter', 'Winter');

function runner(id: string, served: string[], online = true): RunnerRegistryView {
  return {
    runner_id: id,
    runner_name: id,
    added_at: '2026-10-01T00:00:00Z',
    connection: 'online',
    hub_paused: false,
    online,
    projects: served.map((projectId) => ({ entry: projectId, project_id: projectId })),
  } as unknown as RunnerRegistryView;
}

function change(id: number, diff: { field: string; old?: unknown; new?: unknown }[]): ConfigChangeView {
  return {
    id,
    actor: 'ada',
    door: 'board',
    op: 'edit',
    record_key: 'proj_b',
    record_kind: 'project',
    recorded_at: '2026-10-01T00:00:00Z',
    revision: id,
    diff,
  };
}

describe('projects model', () => {
  it('lists every project by name on All, and only the lensed one under a lens', () => {
    expect(projectRows([WINTER, BLIZZARD], null, null).map((row) => row.key)).toEqual(['proj_b', 'proj_w']);
    expect(projectRows([WINTER, BLIZZARD], null, 'proj_w').map((row) => row.key)).toEqual(['proj_w']);
  });

  it('badges a project no live runner serves, and none before the runners are read', () => {
    const served = servedProjectIds([BLIZZARD, WINTER], [runner('r1', ['proj_b']), runner('r2', ['proj_w'], false)]);
    const rows = projectRows([BLIZZARD, WINTER], served, null);
    expect(rows.map((row) => [row.key, row.unserved])).toEqual([
      ['proj_b', false],
      ['proj_w', true],
    ]);
    expect(projectRows([WINTER], null, null)[0].unserved).toBe(false);
    expect(servedProjectIds([WINTER], undefined)).toBeNull();
  });

  it('sends a cleared description as empty text, and leaves unchanged fields out', () => {
    expect(projectPatch({ slug: 'blizzard-hub', description: null })).toEqual({ slug: 'blizzard-hub', description: '' });
    expect(projectPatch({ name: 'Blizzard Hub' })).toEqual({ name: 'Blizzard Hub' });
  });

  it('reads former slugs from the history, newest first, without the current one', () => {
    const history = [
      change(1, [{ field: 'slug', old: 'default', new: 'core' }]),
      change(2, [{ field: 'name', old: 'Default', new: 'Core' }]),
      change(3, [{ field: 'slug', old: 'core', new: 'blizzard' }]),
      change(4, [{ field: 'slug', old: 'blizzard', new: 'default' }]),
    ];
    expect(formerSlugs(history, project('proj_b', 'default', 'Default'))).toEqual(['blizzard', 'core']);
    expect(formerSlugs(undefined, BLIZZARD)).toEqual([]);
  });

  it('lists the links in force — the built-in source first — and drops unlinked ones', () => {
    const sources = [
      { name: 'jira', provider: 'jira', locator: 'acme.atlassian.net', annotate: false, edit: true },
    ] as WorkSourceSummary[];
    const rows = sourceLinkRows(
      [
        { project_id: 'proj_b', name: 'old', linked: false, revision: 2 },
        { project_id: 'proj_b', name: 'jira', linked: true, revision: 1 },
        { project_id: 'proj_b', name: 'hub', linked: true, built_in: true },
      ],
      sources,
    );
    expect(rows.map((row) => [row.name, row.builtIn, row.detail])).toEqual([
      ['hub', true, 'built-in · linked to every project'],
      ['jira', false, 'jira · acme.atlassian.net'],
    ]);
    const repositories = [{ name: 'blizzard', owner: 'acme', repo: 'blizzard', base_branch: 'main' }] as RepositorySummary[];
    expect(
      repositoryLinkRows([{ project_id: 'proj_b', name: 'blizzard', linked: true, revision: 3 }], repositories),
    ).toEqual([{ name: 'blizzard', detail: 'acme/blizzard · main', builtIn: false, revision: 3 }]);
  });

  it('offers only the live records the project does not link yet', () => {
    const linked = linkedNames([
      { project_id: 'proj_b', name: 'jira', linked: true, revision: 1 },
      { project_id: 'proj_b', name: 'gh', linked: false, revision: 2 },
    ]);
    const choices = linkChoices(
      [{ name: 'jira' }, { name: 'gh' }, { name: 'retired', retired: true }, { name: 'hub', built_in: true }],
      linked,
      (name) => (name === 'gh' ? 'github' : ''),
    );
    expect(choices).toEqual([{ value: 'gh', label: 'gh — github' }]);
  });

  it('holds a failure against its own panel only', () => {
    const failure = { kind: 'source' as const, message: 'Unlink failed.' };
    expect(failureFor(failure, 'source')).toBe('Unlink failed.');
    expect(failureFor(failure, 'repository')).toBeNull();
    expect(failureFor(null, 'source')).toBeNull();
  });

  it("waits for a lensed list's links before showing it", () => {
    expect(lensedListState(false, true, 'ready')).toBe('loading');
    expect(lensedListState(true, false, 'ready')).toBe('error');
    expect(lensedListState(false, false, 'empty')).toBe('empty');
  });

  it('calls out what a project lacks, and nothing for a wired one', () => {
    const builtInOnly = [{ project_id: 'proj_b', name: 'hub', linked: true, built_in: true }];
    const missing = missingWiring(builtInOnly, [], false);
    expect(missing).toEqual(['work source', 'repository', 'runner']);
    expect(wiringCallout(missing)).toBe(
      'This project has no work source and no repository and no runner yet. No runner serves it, so its ready work waits in the queue.',
    );
    const wired = missingWiring(
      [{ project_id: 'proj_b', name: 'jira', linked: true, revision: 1 }],
      [{ project_id: 'proj_b', name: 'blizzard', linked: true, revision: 1 }],
      true,
    );
    expect(wiringCallout(wired)).toBeNull();
  });
});
