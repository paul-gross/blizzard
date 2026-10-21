import type { TemplateRef } from '@angular/core';
import type { ChunkCountsView, ConfigChangeView, ProjectView, RepositorySummary, RunnerRegistryView, WorkSourceSummary } from 'fleet';

import {
  failureFor,
  formerSlugs,
  lensedListState,
  listedProjects,
  linkChoices,
  linkedKeys,
  missingWiring,
  pendingUnlinkNames,
  projectLightTone,
  projectCreate,
  projectPatch,
  projectRows,
  repositoryLinkRows,
  servedProjectIds,
  sourceLinkRows,
  wiringCallout,
} from './projects.model';
import { linkCount, projectFacts, projectTabs, queueText } from './project-overview.model';

function project(id: string, slug: string, name: string): ProjectView {
  return { project_id: id, slug, name, description: '', color: 'teal', revision: 1, created_at: '2026-10-01T00:00:00Z' };
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
const REPO_LINK = {
  project_id: 'proj_b',
  repository_id: 'repo_b',
  slug: 'blizzard',
  name: 'Blizzard',
  linked: true,
  revision: 1,
};
const GONE_LINK = { project_id: 'proj_b', name: 'old', linked: false, revision: 2 };

describe('projects model', () => {
  it('lists every project by name on All, and only the lensed one under a lens', () => {
    expect(projectRows([WINTER, BLIZZARD], new Map(), null, null).map((row) => row.key)).toEqual(['proj_b', 'proj_w']);
    expect(projectRows([WINTER, BLIZZARD], new Map(), null, 'proj_w').map((row) => row.key)).toEqual(['proj_w']);
  });

  it("carries each row's project id for its short id", () => {
    expect(projectRows([BLIZZARD, WINTER], new Map(), null, null).map((row) => row.projectId)).toEqual([
      'proj_b',
      'proj_w',
    ]);
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

  it('lists the lensed project alone, and counts only links in force', () => {
    expect(listedProjects([BLIZZARD, WINTER], 'proj_w')).toEqual([WINTER]);
    expect(listedProjects(undefined, null)).toEqual([]);
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
    expect(labels(projectFacts(BLIZZARD, { former: [], counts: undefined, color: undefined, created: undefined, revision: undefined }))).toEqual([
      'Slug',
      'Id',
    ]);
    const full = projectFacts(BLIZZARD, {
      former: ['default'],
      counts: counts(1, 1, 3),
      color: undefined,
      created: undefined,
      revision: undefined,
    });
    expect(labels(full)).toEqual(['Slug', 'Former slugs', 'Queue', 'Id']);
    expect(full.find((fact) => fact.label === 'Queue')?.value).toBe('1 ready · 1 running');
  });

  it('states the color fact once its template is handed in, and a row carries its project color', () => {
    const color = {} as TemplateRef<unknown>;
    const facts = projectFacts(BLIZZARD, { former: [], counts: undefined, color, created: undefined, revision: undefined });
    expect(facts.map((fact) => fact.label)).toEqual(['Slug', 'Color', 'Id']);
    expect(facts.find((fact) => fact.label === 'Color')?.template).toBe(color);
    expect(projectRows([BLIZZARD], new Map(), null, null)[0].color).toBe('teal');
  });

  it("reads the queue with today's done from the read's done_since, not its all-time done", () => {
    expect(queueText({ ...counts(1, 2, 40), done_since: 3 })).toBe('1 ready · 2 running · 3 done today');
    expect(queueText({ ...counts(0, 0, 40), done_since: 0 })).toBe('0 ready · 0 running · 0 done today');
    expect(queueText({ ...counts(0, 2, 40), done_since: null })).toBe('0 ready · 2 running');
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
      {
        work_source_id: 'wsrc_j',
        slug: 'jira',
        name: 'Acme Jira',
        provider: 'jira',
        locator: 'acme.atlassian.net',
        annotate: false,
        edit: true,
      },
    ] as WorkSourceSummary[];
    const rows = sourceLinkRows(
      [
        { project_id: 'proj_b', name: 'wsrc_o', slug: 'old', linked: false, revision: 2 },
        { project_id: 'proj_b', name: 'wsrc_j', slug: 'jira', linked: true, revision: 1 },
        { project_id: 'proj_b', name: 'hub', slug: 'hub', linked: true, built_in: true },
      ],
      sources,
    );
    expect(rows.map((row) => [row.key, row.name, row.builtIn, row.detail])).toEqual([
      ['hub', 'hub', true, 'built-in · linked to every project'],
      ['wsrc_j', 'jira', false, 'Acme Jira · jira · acme.atlassian.net'],
    ]);
    const repositories = [
      { repository_id: 'repo_b', slug: 'blizzard', name: 'Blizzard', owner: 'acme', repo: 'blizzard', base_branch: 'main' },
    ] as RepositorySummary[];
    expect(
      repositoryLinkRows(
        [{ project_id: 'proj_b', repository_id: 'repo_b', slug: 'blizzard', name: 'Blizzard', linked: true, revision: 3 }],
        repositories,
      ),
    ).toEqual([
      { key: 'repo_b', name: 'blizzard', detail: 'Blizzard · acme/blizzard · main', builtIn: false, revision: 3 },
    ]);
  });

  it('offers only the live records the project does not link yet', () => {
    const linked = linkedKeys([
      { project_id: 'proj_b', name: 'jira', linked: true, revision: 1 },
      { project_id: 'proj_b', name: 'gh', linked: false, revision: 2 },
    ]);
    const choices = linkChoices(
      [
        { key: 'jira', name: 'jira' },
        { key: 'gh', name: 'gh' },
        { key: 'retired', name: 'retired', retired: true },
        { key: 'hub', name: 'hub', built_in: true },
      ],
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
      [{ project_id: 'proj_b', repository_id: 'repo_b', slug: 'blizzard', name: 'Blizzard', linked: true, revision: 1 }],
      true,
    );
    expect(wiringCallout(wired)).toBeNull();
  });

  it("tones a project's light by its wiring", () => {
    expect(projectLightTone({ wired: true, needs: null })).toBe('done');
    expect(projectLightTone({ wired: false, needs: 'needs a runner' })).toBe('needs');
    expect(projectLightTone({ wired: false, needs: null })).toBe('idle');
  });

  it("names the links of one project and kind an unlink is in flight for", () => {
    const vars = (projectId: string, kind: 'source' | 'repository', name: string, linked: boolean) => ({
      projectId,
      kind,
      name,
      linked,
      revision: linked ? null : 1,
    });
    const pending = [
      vars('proj_b', 'source', 'wsrc_j', false),
      vars('proj_b', 'source', 'wsrc_g', false),
      vars('proj_b', 'source', 'wsrc_n', true),
      vars('proj_b', 'repository', 'repo_c', false),
      vars('proj_w', 'source', 'wsrc_x', false),
    ];
    expect(pendingUnlinkNames(pending, 'proj_b', 'source')).toEqual(new Set(['wsrc_j', 'wsrc_g']));
    expect(pendingUnlinkNames(pending, 'proj_b', 'repository')).toEqual(new Set(['repo_c']));
    expect(pendingUnlinkNames([], 'proj_b', 'source').size).toBe(0);
    expect(pendingUnlinkNames(pending, null, 'source').size).toBe(0);
  });
});
