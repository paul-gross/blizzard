import type { TemplateRef } from '@angular/core';
import type {
  ChunkCountsView,
  KitFact,
  KitTabOption,
  ProjectView,
  RepositoryLinkView,
  RunnerRegistryView,
  SourceLinkView,
} from 'fleet';

/** A project page's tabs, each kind of link counted once its read answers (`null` until then). */
export function projectTabs(sourceCount: number | null, repositoryCount: number | null): readonly KitTabOption[] {
  return [
    { value: 'overview', label: 'Overview', testid: 'admin-projects-tab-overview' },
    { value: 'sources', label: 'Linked sources', count: sourceCount, testid: 'admin-projects-tab-sources' },
    {
      value: 'repositories',
      label: 'Linked repositories',
      count: repositoryCount,
      testid: 'admin-projects-tab-repositories',
    },
  ];
}

/** How many links in force a read holds, `null` until it answers. */
export function linkCount(links: readonly (SourceLinkView | RepositoryLinkView)[] | undefined): number | null {
  return links ? links.filter((link) => link.linked).length : null;
}

/** The project's queue: `1 ready · 1 running · 3 done today`, today's done counted from the read's own
 * `done_since` — left off when the read carries none. */
export function queueText(counts: ChunkCountsView): string {
  const queue = `${counts.ready} ready · ${counts.running} running`;
  return counts.done_since === null || counts.done_since === undefined ? queue : `${queue} · ${counts.done_since} done today`;
}

/** One line of an overview card: a linked record or a serving runner, and what it is. */
export interface OverviewLineVm {
  readonly name: string;
  readonly detail: string;
}

/** The overview's Served by card: each serving runner and its connection. */
export function servedByLines(runners: readonly RunnerRegistryView[]): readonly OverviewLineVm[] {
  return runners.map((runner) => ({ name: runner.runner_name, detail: runner.connection }));
}

/** What the overview's facts read besides the project itself. */
export interface ProjectFactsInput {
  /** The slugs the project left, newest first. */
  readonly former: readonly string[];
  /** The project's chunk counts, once read. */
  readonly counts: ChunkCountsView | undefined;
  /** Renders when it was created. */
  readonly created: TemplateRef<unknown> | undefined;
  /** Renders its color: the swatch and the palette name. */
  readonly color: TemplateRef<unknown> | undefined;
  /** Renders its revision: who last changed it, through which door. */
  readonly revision: TemplateRef<unknown> | undefined;
}

/** The project's facts: its slug and the slugs it left, its color, when it was made, its queue, its revision and
 * its id. A fact whose read has not answered is left out rather than shown empty. */
export function projectFacts(project: ProjectView | null, input: ProjectFactsInput): readonly KitFact[] {
  if (project === null) return [];
  const facts: KitFact[] = [];
  facts.push({ label: 'Slug', value: project.slug, testid: 'admin-projects-slug' });
  if (input.former.length > 0) {
    facts.push({ label: 'Former slugs', value: input.former.join(', '), testid: 'admin-projects-former-slugs' });
  }
  if (input.color) facts.push({ label: 'Color', template: input.color, testid: 'admin-projects-color' });
  if (input.created) facts.push({ label: 'Created', template: input.created, testid: 'admin-projects-created' });
  if (input.counts) facts.push({ label: 'Queue', value: queueText(input.counts), testid: 'admin-projects-queue' });
  if (input.revision) facts.push({ label: 'Revision', template: input.revision, testid: 'admin-projects-revision' });
  facts.push({ label: 'Id', value: project.project_id, testid: 'admin-projects-id' });
  return facts;
}
