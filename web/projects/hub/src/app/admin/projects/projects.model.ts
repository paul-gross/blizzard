import type { TemplateRef } from '@angular/core';
import type {
  ConfigChangeView,
  KitAsyncStateValue,
  KitFact,
  ProjectPatchRequest,
  ProjectView,
  RepositoryLinkView,
  RepositorySummary,
  RunnerRegistryView,
  SourceLinkView,
  WorkSourceSummary,
} from 'fleet';

import { servingRunners } from '../../core/project-serving';
import type { ConfigFieldDef, ConfigFormValues } from '../config-edit.model';

/** A project's form: its handle, its display name and what it is for. Every field stays
 * editable — a slug change keeps the old one resolving until another project takes it. */
export const PROJECT_FIELDS: readonly ConfigFieldDef[] = [
  { key: 'slug', label: 'Slug', kind: 'text', required: true },
  { key: 'name', label: 'Name', kind: 'text', required: true },
  { key: 'description', label: 'Description', kind: 'text' },
];

/** What the slug field means, under the form. */
export const SLUG_HINT =
  'Lowercase letters, digits and single hyphens. A former slug keeps opening this project until another project takes it.';

/** One row of the projects list. The key is the project's id: nothing holds a slug as a key. */
export interface ProjectRowVm {
  readonly key: string;
  readonly name: string;
  readonly slug: string;
  readonly description: string;
  readonly revision: number;
  /** Whether no live runner serves the project, so its ready work waits. */
  readonly unserved: boolean;
}

/** The projects list, by display name; under a lens, only the lensed project. */
export function projectRows(
  projects: readonly ProjectView[],
  servedIds: ReadonlySet<string> | null,
  lensId: string | null,
): readonly ProjectRowVm[] {
  return projects
    .filter((project) => lensId === null || project.project_id === lensId)
    .map((project) => ({
      key: project.project_id,
      name: project.name,
      slug: project.slug,
      description: project.description,
      revision: project.revision,
      unserved: servedIds !== null && !servedIds.has(project.project_id),
    }))
    .sort((a, b) => a.name.localeCompare(b.name) || a.slug.localeCompare(b.slug));
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
  readonly name: string;
  /** What the linked record is, in a few words. */
  readonly detail: string;
  /** Whether the link is the built-in source's, which every project has and none unlinks. */
  readonly builtIn: boolean;
  readonly revision: number | null;
}

/** The names of the links in force — an unlinked link stays in the listing with `linked: false`. */
export function linkedNames(links: readonly (SourceLinkView | RepositoryLinkView)[]): ReadonlySet<string> {
  return new Set(links.filter((link) => link.linked).map((link) => link.name));
}

/** A source as its link row describes it. */
export function sourceDetail(source: WorkSourceSummary | undefined): string {
  if (!source) return '';
  return [source.provider, source.locator].filter((part) => !!part).join(' · ');
}

/** A repository as its link row describes it. */
export function repositoryDetail(repository: RepositorySummary | undefined): string {
  if (!repository) return '';
  return `${repository.owner}/${repository.repo} · ${repository.base_branch}`;
}

/** The source links in force, built-in first, then by name. */
export function sourceLinkRows(
  links: readonly SourceLinkView[],
  sources: readonly WorkSourceSummary[],
): readonly ProjectLinkRowVm[] {
  const byName = new Map(sources.map((source) => [source.name, source]));
  return links
    .filter((link) => link.linked)
    .map((link) => ({
      name: link.name,
      detail: link.built_in ? 'built-in · linked to every project' : sourceDetail(byName.get(link.name)),
      builtIn: link.built_in === true,
      revision: link.revision ?? null,
    }))
    .sort((a, b) => Number(b.builtIn) - Number(a.builtIn) || a.name.localeCompare(b.name));
}

/** The repository links in force, by name. */
export function repositoryLinkRows(
  links: readonly RepositoryLinkView[],
  repositories: readonly RepositorySummary[],
): readonly ProjectLinkRowVm[] {
  const byName = new Map(repositories.map((repository) => [repository.name, repository]));
  return links
    .filter((link) => link.linked)
    .map((link) => ({
      name: link.name,
      detail: repositoryDetail(byName.get(link.name)),
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
  records: readonly { readonly name: string; readonly retired?: boolean | null; readonly built_in?: boolean | null }[],
  linked: ReadonlySet<string>,
  describe: (name: string) => string,
): readonly LinkChoiceVm[] {
  return records
    .filter((record) => !record.retired && !record.built_in && !linked.has(record.name))
    .map((record) => {
      const detail = describe(record.name);
      return { value: record.name, label: detail ? `${record.name} — ${detail}` : record.name };
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

/** The overview's callout for what a project lacks, or `null` when it is wired. */
export function wiringCallout(missing: readonly string[]): string | null {
  if (missing.length === 0) return null;
  const lacks = `This project has no ${missing.join(' and no ')} yet.`;
  return missing.includes('runner') ? `${lacks} No runner serves it, so its ready work waits in the queue.` : lacks;
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
): string | null {
  if (!sourceLinks || !repositoryLinks || !runnersRead) return null;
  return wiringCallout(missingWiring(sourceLinks, repositoryLinks, served));
}

/** The project's facts: its slug and the slugs it left, what it is for, its id, and its
 * revision rendered by `revision` (who last changed it, through which door). */
export function projectFacts(
  project: ProjectView | null,
  former: readonly string[],
  revision: TemplateRef<unknown> | undefined,
): readonly KitFact[] {
  if (project === null) return [];
  const facts: KitFact[] = [
    { label: 'Slug', value: project.slug, testid: 'admin-projects-slug' },
    { label: 'Former slugs', value: former.length > 0 ? former.join(', ') : '—', testid: 'admin-projects-former-slugs' },
    { label: 'Description', value: project.description || '—' },
    { label: 'Id', value: project.project_id },
  ];
  if (revision) facts.push({ label: 'Revision', template: revision, testid: 'admin-projects-revision' });
  return facts;
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
  const byName = new Map((sources ?? []).map((source) => [source.name, source]));
  return linkChoices(sources ?? [], linkedNames(links ?? []), (name) => sourceDetail(byName.get(name)));
}

/** The repositories a link dialog offers. */
export function repositoryChoices(
  repositories: readonly RepositorySummary[] | undefined,
  links: readonly RepositoryLinkView[] | undefined,
): readonly LinkChoiceVm[] {
  const byName = new Map((repositories ?? []).map((repository) => [repository.name, repository]));
  return linkChoices(repositories ?? [], linkedNames(links ?? []), (name) => repositoryDetail(byName.get(name)));
}

/** A tenant list's state under the lens: the lensed links' failure or wait first, then the list's own. */
export function lensedListState(failed: boolean, pending: boolean, list: KitAsyncStateValue): KitAsyncStateValue {
  if (failed) return 'error';
  return pending ? 'loading' : list;
}
