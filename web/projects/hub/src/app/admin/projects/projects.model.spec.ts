import type { ChunkCountsView, ConfigChangeView, ProjectView, RepositorySummary, RunnerRegistryView, WorkSourceSummary } from 'fleet';

import {
  failureFor,
  formerSlugs,
  lensedListState,
  listedProjects,
  linkChoices,
  linkedNames,
  missingWiring,
  projectCreate,
  projectPatch,
  projectRows,
  projectsCaption,
  repositoryLinkRows,
  servedProjectIds,
  sourceLinkRows,
  wiringCallout,
} from './projects.model';
import { linkCount, projectFacts, projectTabs, queueText } from './project-overview.model';

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

function counts(ready: number, running: number, done: number): ChunkCountsView {
  return {
    ready,
    running,
    done,
    delivering: 0,
    needs_human: 0,
    not_ready: 0,
    paused: 0,
    stopped: 0,
    terminal: done,
    total: ready + running + done,
  } as ChunkCountsView;
}

const JIRA_LINK = { project_id: 'proj_b', name: 'jira', linked: true, revision: 1 };
const HUB_LINK = { project_id: 'proj_b', name: 'hub', linked: true, built_in: true };
const REPO_LINK = { project_id: 'proj_b', name: 'blizzard', linked: true, revision: 1 };
const GONE_LINK = { project_id: 'proj_b', name: 'old', linked: false, revision: 2 };

describe('projects model', () => {
  it('lists every project by name on All, and only the lensed one under a lens', () => {
    expect(projectRows([WINTER, BLIZZARD], new Map(), null, null).map((row) => row.key)).toEqual(['proj_b', 'proj_w']);
    expect(projectRows([WINTER, BLIZZARD], new Map(), null, 'proj_w').map((row) => row.key)).toEqual(['proj_w']);
  });

  it("counts each row's links in force and its queue, each once read", () => {
    const figures = new Map([
      [
        'proj_b',
        {
          sourceLinks: [HUB_LINK, JIRA_LINK, GONE_LINK],
          repositoryLinks: [REPO_LINK],
          counts: counts(2, 3, 9),
        },
      ],
      ['proj_w', { sourceLinks: [HUB_LINK] }],
    ]);
    const rows = projectRows([BLIZZARD, WINTER], figures, null, null);
    expect(rows[0].figures).toEqual(['2 linked sources', '1 linked repo', '5 in queue']);
    expect(rows[1].figures).toEqual(['1 linked source']);
  });

  it('marks a wired project, names what a wanting one needs, and judges neither before its reads answer', () => {
    const served = servedProjectIds([BLIZZARD, WINTER], [runner('r1', ['proj_b']), runner('r2', ['proj_w'], false)]);
    const figures = new Map([
      ['proj_b', { sourceLinks: [HUB_LINK, JIRA_LINK], repositoryLinks: [REPO_LINK] }],
      ['proj_w', { sourceLinks: [HUB_LINK], repositoryLinks: [] }],
    ]);
    const rows = projectRows([BLIZZARD, WINTER], figures, served, null);
    expect(rows.map((row) => [row.key, row.wired, row.needs])).toEqual([
      ['proj_b', true, null],
      ['proj_w', false, 'needs a work source, a repository, a runner'],
    ]);
    const unread = projectRows([BLIZZARD], figures, null, null)[0];
    expect([unread.wired, unread.needs]).toEqual([false, null]);
    expect(projectRows([BLIZZARD], new Map(), served, null)[0].needs).toBeNull();
    expect(servedProjectIds([WINTER], undefined)).toBeNull();
  });

  it('lists the lensed project alone, captions the list by tenant, and counts only links in force', () => {
    expect(listedProjects([BLIZZARD, WINTER], 'proj_w')).toEqual([WINTER]);
    expect(listedProjects(undefined, null)).toEqual([]);
    expect(projectsCaption('grosscode')).toBe('Projects in grosscode');
    expect(projectsCaption('')).toBe('Projects');
    expect(linkCount([JIRA_LINK, GONE_LINK])).toBe(1);
    expect(linkCount(undefined)).toBeNull();
    expect(projectTabs(2, null).map((tab) => [tab.label, tab.count ?? null])).toEqual([
      ['Overview', null],
      ['Linked sources', 2],
      ['Linked repositories', null],
    ]);
  });

  it('creates a project named by its slug when the name is left empty', () => {
    expect(projectCreate({ slug: 'nova' })).toEqual({ slug: 'nova', name: 'nova', description: '' });
    expect(projectCreate({ slug: 'nova', name: 'Nova', description: 'New work' })).toEqual({
      slug: 'nova',
      name: 'Nova',
      description: 'New work',
    });
  });

  it("states the overview's facts, leaving out the ones not yet read", () => {
    const labels = (facts: readonly { label: string }[]) => facts.map((fact) => fact.label);
    expect(labels(projectFacts(BLIZZARD, { tenant: '', former: [], counts: undefined, created: undefined, revision: undefined }))).toEqual([
      'Slug',
      'Id',
    ]);
    const full = projectFacts(BLIZZARD, {
      tenant: 'grosscode',
      former: ['default'],
      counts: counts(1, 1, 3),
      created: undefined,
      revision: undefined,
    });
    expect(labels(full)).toEqual(['Tenant', 'Slug', 'Former slugs', 'Queue', 'Id']);
    expect(full.find((fact) => fact.label === 'Queue')?.value).toBe('1 ready · 1 running · 3 done');
    expect(queueText(counts(0, 2, 0))).toBe('0 ready · 2 running · 0 done');
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
      'This project has no work source and no repository and no runner yet. Its ready work waits in the queue until a runner serves it.',
    );
    expect(wiringCallout(['runner'], 2)).toBe(
      'This project has no runner yet. Its 2 ready chunks wait in the queue until a runner serves it.',
    );
    expect(wiringCallout(['runner'], 1)).toBe(
      'This project has no runner yet. Its 1 ready chunk waits in the queue until a runner serves it.',
    );
    expect(wiringCallout(['repository'], 4)).toBe('This project has no repository yet.');
    const wired = missingWiring(
      [{ project_id: 'proj_b', name: 'jira', linked: true, revision: 1 }],
      [{ project_id: 'proj_b', name: 'blizzard', linked: true, revision: 1 }],
      true,
    );
    expect(wiringCallout(wired)).toBeNull();
  });
});
