import type {
  ConfigChangeView,
  KitAsyncStateValue,
  ProjectCreateRequest,
  ProjectPatchRequest,
  ProjectView,
  RepositoryLinkView,
  RepositorySummary,
  RunnerRegistryView,
  SourceLinkView,
  Tone,
  WorkSourceSummary,
} from 'fleet';

import { servingRunners } from '../../core/project-serving';
import type { ConfigFieldDef, ConfigFormValues } from '../config-edit.model';
import { workSourceKey, workSourceSlug } from '../work-sources/work-sources.model';
import type { ProjectLinkKind, ProjectLinkVars } from './projects.mutations';

/** A project's form: its handle, its display name and what it is for. Every field stays
 * editable — a slug change keeps the old one resolving until another project takes it. A create
 * may leave the name out: it defaults to the slug. */
export const PROJECT_FIELDS: readonly ConfigFieldDef[] = [
  { key: 'slug', label: 'Slug', kind: 'text', required: true },
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'description', label: 'Description', kind: 'text' },
];

/** The create a new project's form sends: a name left empty is the slug. */
export function projectCreate(body: Readonly<Record<string, string | boolean | null>>): ProjectCreateRequest {
  const text = (key: string): string => (typeof body[key] === 'string' ? (body[key] as string).trim() : '');
  const slug = text('slug');
  return { slug, name: text('name') || slug, description: text('description') };
}

/** What the slug field means, under the form. */
export const SLUG_HINT =
  'Lowercase letters, digits and single hyphens. A former slug keeps opening this project until another project takes it.';

/** The projects a list shows: every one on All, only the lensed one under a lens. */
export function listedProjects(projects: readonly ProjectView[] | undefined, lensId: string | null): readonly ProjectView[] {
  return (projects ?? []).filter((project) => lensId === null || project.project_id === lensId);
}

/** What one project's own reads hold for its list row — each `undefined` until it answers. */
export interface ProjectFigures {
  readonly sourceLinks?: readonly SourceLinkView[];
  readonly repositoryLinks?: readonly RepositoryLinkView[];
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
  projects: readonly ProjectView[],
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
  projects: readonly ProjectView[],
  runners: readonly RunnerRegistryView[] | undefined,
): ReadonlySet<string> | null {
  if (runners === undefined) return null;
  return new Set(
    projects
      .filter((project) => servingRunners(project.project_id, runners).length > 0)
      .map((project) => project.project_id),
  );
}

/** The patch an edit sends. A cleared description is sent as empty text: on a project `null`
 * means "leave it", and the description may be empty. */
export function projectPatch(body: Readonly<Record<string, string | boolean | null>>): ProjectPatchRequest {
  const patch: ProjectPatchRequest = {};
  for (const [key, value] of Object.entries(body)) {
    if (key === 'slug' || key === 'name') patch[key] = typeof value === 'string' ? value : null;
    if (key === 'description') patch.description = typeof value === 'string' ? value : '';
  }
  return patch;
}

/** The form values a create starts from, or an edit shows. */
export function projectFormValues(project: ProjectView | null): ConfigFormValues {
  return {
    slug: project?.slug ?? '',
    name: project?.name ?? '',
    description: project?.description ?? '',
  };
}

/** The slugs a project has moved away from, newest first and without repeats, read from its
 * own change history. The project's current slug is left out: a slug taken back is current. */
export function formerSlugs(
  changes: readonly ConfigChangeView[] | undefined,
  project: ProjectView | null,
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
}

/** What a link names its record by: a repository's id, a work source's id (or `hub`). */
export function linkKey(link: SourceLinkView | RepositoryLinkView): string {
  return 'repository_id' in link ? link.repository_id : link.name;
}

/** The keys of the links in force — an unlinked link stays in the listing with `linked: false`. */
export function linkedKeys(links: readonly (SourceLinkView | RepositoryLinkView)[]): ReadonlySet<string> {
  return new Set(links.filter((link) => link.linked).map(linkKey));
}

/** A source as its link row describes it. */
export function sourceDetail(source: WorkSourceSummary | undefined): string {
  if (!source) return '';
  const name = source.name === workSourceSlug(source) ? null : source.name;
  return [name, source.provider, source.locator].filter((part) => !!part).join(' · ');
}

/** A repository as its link row describes it. */
export function repositoryDetail(repository: RepositorySummary | undefined): string {
  if (!repository) return '';
  return `${repository.name} · ${repository.owner}/${repository.repo} · ${repository.base_branch}`;
}

/** The source links in force, built-in first, then by slug. */
export function sourceLinkRows(
  links: readonly SourceLinkView[],
  sources: readonly WorkSourceSummary[],
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
    }))
    .sort((a, b) => Number(b.builtIn) - Number(a.builtIn) || a.name.localeCompare(b.name));
}

/** The repository links in force, by slug. */
export function repositoryLinkRows(
  links: readonly RepositoryLinkView[],
  repositories: readonly RepositorySummary[],
): readonly ProjectLinkRowVm[] {
  const byId = new Map(repositories.map((repository) => [repository.repository_id, repository]));
  return links
    .filter((link) => link.linked)
    .map((link) => ({
      key: link.repository_id,
      name: link.slug,
      detail: repositoryDetail(byId.get(link.repository_id)),
      builtIn: false,
      revision: link.revision,
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
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
  sourceLinks: readonly SourceLinkView[],
  repositoryLinks: readonly RepositoryLinkView[],
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
  runners: readonly RunnerRegistryView[] | undefined,
): readonly RunnerRegistryView[] {
  return projectId === null ? [] : servingRunners(projectId, runners ?? []);
}

/** The overview's callout once every read it judges by has answered, else `null`. */
export function overviewCallout(
  sourceLinks: readonly SourceLinkView[] | undefined,
  repositoryLinks: readonly RepositoryLinkView[] | undefined,
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
  sources: readonly WorkSourceSummary[] | undefined,
  links: readonly SourceLinkView[] | undefined,
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
  repositories: readonly RepositorySummary[] | undefined,
  links: readonly RepositoryLinkView[] | undefined,
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
