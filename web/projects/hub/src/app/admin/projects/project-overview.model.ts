import type {
  ChunkCountsView,
  KitTabOption,
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
