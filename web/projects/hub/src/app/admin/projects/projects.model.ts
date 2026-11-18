import type { KitAsyncStateValue, Tone } from 'fleet';

import type { Project } from '../../core/project.domain';
import { servingProjectRunners, type ProjectRunner } from '../../core/runners.domain';
import type { ConfigChange } from '../config-history.domain';
import type { ProjectRepository } from '../repositories/repositories.domain';
import type { ProjectWorkSource } from '../work-sources/work-sources.domain';
import type { ProjectLinkKind, ProjectLinkVars } from './projects.mutations';
import type { ProjectFigures, ProjectRepositoryLink, ProjectSourceLink } from './project-read.domain';

const workSourceKey = (source: ProjectWorkSource): string => source.work_source_id ?? source.name;
const workSourceSlug = (source: ProjectWorkSource): string => source.slug ?? source.name;

/** What the slug field means, under the form. */
export const SLUG_HINT =
  'Lowercase letters, digits and single hyphens. A former slug keeps opening this project until another project takes it.';

/** Resolve the selected lens against the settled project read. */
export function projectIdForSlug(projects: readonly Project[] | undefined, slug: string | null): string | null {
  return projects?.find((project) => project.slug === slug)?.project_id ?? null;
}

/** The projects a list shows: every one on All, only the lensed one under a lens. */
export function listedProjects(projects: readonly Project[] | undefined, lensId: string | null): readonly Project[] {
  return (projects ?? []).filter((project) => lensId === null || project.project_id === lensId);
}

/** One row of the projects list. The key is the project's id: nothing holds a slug as a key. */
export interface ProjectRowVm {
  readonly key: string;
  readonly name: string;
  /** The project's id, which the card's short id is built from. */
  readonly projectId: string;
  /** The project's palette color. */
  readonly color: string;
  /** What it still needs to take work in, land it and get it done (`needs a runner`), `null` while it is wired or
   * not yet judged. */
  readonly needs: string | null;
  /** Whether every read the judgment rests on has answered and the project lacks nothing. */
  readonly wired: boolean;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** What a project lacks, or `null` until its links and the runners are all read. */
export function rowMissing(figures: ProjectFigures | undefined, served: boolean | null): readonly string[] | null {
  if (!figures?.sourceLinks || !figures.repositoryLinks || served === null) return null;
  return missingWiring(figures.sourceLinks, figures.repositoryLinks, served);
}

/** The list's word for what a project lacks: `needs a work source, a runner`. */
export function needsText(missing: readonly string[]): string {
  return `needs a ${missing.join(', a ')}`;
}

/** The projects list, by display name; under a lens, only the lensed project. A project is judged wired or wanting
 * only once its links and the runners are read. */
export function projectRows(
  projects: readonly Project[],
  figures: ReadonlyMap<string, ProjectFigures>,
  servedIds: ReadonlySet<string> | null,
  lensId: string | null,
): readonly ProjectRowVm[] {
  return projects
    .filter((project) => lensId === null || project.project_id === lensId)
    .sort((a, b) => a.name.localeCompare(b.name) || a.slug.localeCompare(b.slug))
    .map((project) => {
      const own = figures.get(project.project_id);
      const missing = rowMissing(own, servedIds === null ? null : servedIds.has(project.project_id));
      return {
        key: project.project_id,
        name: project.name,
        projectId: project.project_id,
        color: project.color,
        needs: missing !== null && missing.length > 0 ? needsText(missing) : null,
        wired: missing !== null && missing.length === 0,
      };
    });
}

/** The ids of the projects at least one live runner serves, or `null` before the runners are read. */
export function servedProjectIds(
  projects: readonly Project[],
  runners: readonly ProjectRunner[] | undefined,
): ReadonlySet<string> | null {
  if (runners === undefined) return null;
  return new Set(
    projects
      .filter((project) => servingProjectRunners(project.project_id, runners).length > 0)
      .map((project) => project.project_id),
  );
}

/** The slugs a project has moved away from, newest first and without repeats, read from its
 * own change history. The project's current slug is left out: a slug taken back is current. */
export function formerSlugs(
  changes: readonly ConfigChange[] | undefined,
  project: Project | null,
): readonly string[] {
  if (!changes || project === null) return [];
  const current = project.slug;
  const left: string[] = [];
  const ordered = [...changes].sort((a, b) => b.id - a.id);
  for (const change of ordered) {
    for (const field of change.diff) {
      if (field.field !== 'slug' || typeof field.old !== 'string' || field.old === '') continue;
      if (field.old !== current && !left.includes(field.old)) left.push(field.old);
    }
  }
  return left;
}

/** One stored link a project holds, as its panel lists it. */
export interface ProjectLinkRowVm {
  /** What a link call names: a source's or a repository's id (`hub` for the built-in source). */
  readonly key: string;
  /** What the row shows: the linked record's current slug. */
  readonly name: string;
  /** What the linked record is, in a few words. */
  readonly detail: string;
  /** Whether the link is the built-in source's, which every project has and none unlinks. */
  readonly builtIn: boolean;
  readonly revision: number | null;
  /** The branch a repository link lands into; a source link has none. */
  readonly branch: LinkBranchVm | null;
}

/** A repository link's effective branch: its own override, else the repository's `base_branch` it inherits. */
export interface LinkBranchVm {
  readonly name: string;
  readonly marker: 'inherited' | 'override';
  /** The link's own branch, `null` while it inherits — what the edit dialog starts from. */
  readonly override: string | null;
  /** The repository's own `base_branch`, which a cleared override inherits; `null` while the catalogue is unread. */
  readonly inherits: string | null;
}

/** A repository link's branch as its row shows it, beside the repository record it inherits from. */
export function linkBranch(link: ProjectRepositoryLink, repository: ProjectRepository | undefined): LinkBranchVm {
  return {
    name: link.effective_base_branch,
    marker: link.base_branch === null ? 'inherited' : 'override',
    override: link.base_branch,
    inherits: repository?.base_branch ?? null,
  };
}

/** What a link names its record by: a repository's id, a work source's id (or `hub`). */
export function linkKey(link: ProjectSourceLink | ProjectRepositoryLink): string {
  return 'repository_id' in link ? link.repository_id : link.name;
}

/** The keys of the links in force — an unlinked link stays in the listing with `linked: false`. */
export function linkedKeys(links: readonly (ProjectSourceLink | ProjectRepositoryLink)[]): ReadonlySet<string> {
  return new Set(links.filter((link) => link.linked).map(linkKey));
}

/** A source as its link row describes it. */
export function sourceDetail(source: ProjectWorkSource | undefined): string {
  if (!source) return '';
  const name = source.name === workSourceSlug(source) ? null : source.name;
  return [name, source.provider, source.locator].filter((part) => !!part).join(' · ');
}

/** A repository as a link choice describes it, with the branch a new link inherits. */
export function repositoryDetail(repository: ProjectRepository | undefined): string {
  if (!repository) return '';
  return `${repositoryIdentity(repository)} · ${repository.base_branch}`;
}

/** A repository as its link row describes it; the row shows the link's own effective branch beside it. */
export function repositoryIdentity(repository: ProjectRepository | undefined): string {
  if (!repository) return '';
  return `${repository.name} · ${repository.owner}/${repository.repo}`;
}

/** The source links in force, built-in first, then by slug. */
export function sourceLinkRows(
  links: readonly ProjectSourceLink[],
  sources: readonly ProjectWorkSource[],
): readonly ProjectLinkRowVm[] {
  const byKey = new Map(sources.map((source) => [workSourceKey(source), source]));
  return links
    .filter((link) => link.linked)
    .map((link) => ({
      key: link.name,
      name: link.slug ?? link.name,
      detail: link.built_in ? 'built-in · linked to every project' : sourceDetail(byKey.get(link.name)),
      builtIn: link.built_in === true,
      revision: link.revision ?? null,
      branch: null,
    }))
    .sort((a, b) => Number(b.builtIn) - Number(a.builtIn) || a.name.localeCompare(b.name));
}

/** The repository links in force, in the project's landing order as the hub lists them. */
export function repositoryLinkRows(
  links: readonly ProjectRepositoryLink[],
  repositories: readonly ProjectRepository[],
): readonly ProjectLinkRowVm[] {
  const byId = new Map(repositories.map((repository) => [repository.repository_id, repository]));
  return links
    .filter((link) => link.linked)
    .map((link) => ({
      key: link.repository_id,
      name: link.slug,
      detail: repositoryIdentity(byId.get(link.repository_id)),
      builtIn: false,
      revision: link.revision,
      branch: linkBranch(link, byId.get(link.repository_id)),
    }));
}

/** One record a link dialog offers. */
export interface LinkChoiceVm {
  readonly value: string;
  readonly label: string;
}

/** The live records of a kind the project does not link yet — a retired record takes no new link
 * from the board, and the built-in source is linked already. */
export function linkChoices(
  records: readonly {
    readonly key: string;
    readonly name: string;
    readonly retired?: boolean | null;
    readonly built_in?: boolean | null;
  }[],
  linked: ReadonlySet<string>,
  describe: (key: string) => string,
): readonly LinkChoiceVm[] {
  return records
    .filter((record) => !record.retired && !record.built_in && !linked.has(record.key))
    .map((record) => {
      const detail = describe(record.key);
      return { value: record.key, label: detail ? `${record.name} — ${detail}` : record.name };
    })
    .sort((a, b) => a.value.localeCompare(b.value));
}

/** What a project still lacks to take work in, land it and get it done — the wiring the
 * overview calls out. The built-in source alone draws nothing from a tracker. */
export function missingWiring(
  sourceLinks: readonly ProjectSourceLink[],
  repositoryLinks: readonly ProjectRepositoryLink[],
  served: boolean,
): readonly string[] {
  const missing: string[] = [];
  if (!sourceLinks.some((link) => link.linked && !link.built_in)) missing.push('work source');
  if (!repositoryLinks.some((link) => link.linked)) missing.push('repository');
  if (!served) missing.push('runner');
  return missing;
}

/** The overview's callout for what a project lacks, or `null` when it is wired. With no runner it says how many of
 * its chunks wait, once `ready` is read. */
export function wiringCallout(missing: readonly string[], ready: number | null = null): string | null {
  if (missing.length === 0) return null;
  const lacks = `This project has no ${missing.join(' and no ')} yet.`;
  if (!missing.includes('runner')) return lacks;
  const waits =
    ready === null || ready === 0
      ? 'Its ready work waits in the queue until a runner serves it.'
      : `Its ${plural(ready, 'ready chunk waits', 'ready chunks wait')} in the queue until a runner serves it.`;
  return `${lacks} ${waits}`;
}

/** The live runners serving the project, none while nothing is selected. */
export function projectServedBy(
  projectId: string | null,
  runners: readonly ProjectRunner[] | undefined,
): readonly ProjectRunner[] {
  return projectId === null ? [] : servingProjectRunners(projectId, runners ?? []);
}

/** The overview's callout once every read it judges by has answered, else `null`. */
export function overviewCallout(
  sourceLinks: readonly ProjectSourceLink[] | undefined,
  repositoryLinks: readonly ProjectRepositoryLink[] | undefined,
  runnersRead: boolean,
  served: boolean,
  ready: number | null = null,
): string | null {
  if (!sourceLinks || !repositoryLinks || !runnersRead) return null;
  return wiringCallout(missingWiring(sourceLinks, repositoryLinks, served), ready);
}

/** A failed write held against the panel it came from. */
export interface PanelFailure<K extends string> {
  readonly kind: K;
  readonly message: string;
}

/** The failure `kind`'s panel shows, if the last one is its. */
export function failureFor<K extends string>(failure: PanelFailure<K> | null, kind: K): string | null {
  return failure !== null && failure.kind === kind ? failure.message : null;
}

/** The work sources a link dialog offers. */
export function sourceChoices(
  sources: readonly ProjectWorkSource[] | undefined,
  links: readonly ProjectSourceLink[] | undefined,
): readonly LinkChoiceVm[] {
  const byKey = new Map((sources ?? []).map((source) => [workSourceKey(source), source]));
  return linkChoices(
    (sources ?? []).map((source) => ({ ...source, key: workSourceKey(source), name: workSourceSlug(source) })),
    linkedKeys(links ?? []),
    (key) => sourceDetail(byKey.get(key)),
  );
}

/** The repositories a link dialog offers. */
export function repositoryChoices(
  repositories: readonly ProjectRepository[] | undefined,
  links: readonly ProjectRepositoryLink[] | undefined,
): readonly LinkChoiceVm[] {
  const byId = new Map((repositories ?? []).map((repository) => [repository.repository_id, repository]));
  return linkChoices(
    (repositories ?? []).map((repository) => ({ ...repository, key: repository.repository_id, name: repository.slug })),
    linkedKeys(links ?? []),
    (id) => repositoryDetail(byId.get(id)),
  );
}

/** A tenant list's state under the lens: the lensed links' failure or wait first, then the list's own. */
export function lensedListState(failed: boolean, pending: boolean, list: KitAsyncStateValue): KitAsyncStateValue {
  if (failed) return 'error';
  return pending ? 'loading' : list;
}

/** The tone a project row's status light takes: green once wired, red while wanting, neutral while not yet judged. */
export function projectLightTone(row: Pick<ProjectRowVm, 'wired' | 'needs'>): Tone {
  if (row.wired) return 'done';
  return row.needs !== null ? 'needs' : 'idle';
}

/** The names of the `kind` links of `projectId` an unlink is in flight for — the rows whose Unlink waits. */
export function pendingUnlinkNames(
  pending: readonly ProjectLinkVars[],
  projectId: string | null,
  kind: ProjectLinkKind,
): ReadonlySet<string> {
  return new Set(
    pending
      .filter((vars) => !vars.linked && vars.kind === kind && vars.projectId === projectId)
      .map((vars) => vars.name),
  );
}
