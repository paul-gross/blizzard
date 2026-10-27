import { RecordKind, type RepositoryCreateRequest, type RepositorySummary } from 'fleet';

import { type ConfigFieldDef, createText } from '../config-edit.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { factText, recordLink, secretLinks } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';

/** The create a new repository's form sends: a name left empty is omitted, and the hub takes the slug. */
export function repositoryCreate(body: Readonly<Record<string, string | boolean>>): RepositoryCreateRequest {
  const text = (key: string): string => createText(body, key);
  const name = text('name');
  return {
    slug: text('slug'),
    ...(name === '' ? {} : { name }),
    forge: text('forge'),
    owner: text('owner'),
    repo: text('repo'),
    base_branch: text('base_branch'),
    secret_name: text('secret_name'),
  };
}

/** A repository's badges — retired. */
export function repositoryBadges(repository: RepositorySummary): readonly ConfigBadgeVm[] {
  return repository.retired ? [{ label: 'retired', tone: 'idle' }] : [];
}

/** The repository list's rows under `filter`. */
export function repositoryRows(
  repositories: readonly RepositorySummary[],
  filter: LifecycleFilter,
): readonly ConfigRowVm[] {
  return filterByLifecycle(repositories, filter).map((repository) => ({
    key: repository.repository_id,
    title: repository.slug,
    sub: [
      ...(repository.name === repository.slug ? [] : [repository.name]),
      `${repository.owner}/${repository.repo}`,
      `base ${repository.base_branch}`,
    ],
    badges: repositoryBadges(repository),
    revision: repository.revision,
    retired: repository.retired ?? false,
  }));
}

/** A repository's detail — `null` with none loaded. */
export function repositoryRecordVm(repository: RepositorySummary | undefined): ConfigRecordVm | null {
  if (!repository) return null;
  return {
    name: repository.slug,
    badges: repositoryBadges(repository),
    facts: [
      { label: 'Name', value: factText(repository.name) },
      { label: 'Forge', value: factText(repository.forge_name ?? repository.forge_slug) },
      { label: 'Owner', value: factText(repository.owner) },
      { label: 'Repository', value: factText(repository.repo) },
      { label: 'Base branch', value: factText(repository.base_branch) },
      { label: 'Created by', value: factText(repository.created_by_name ?? repository.created_by) },
    ],
    revision: repository.revision,
    note: repository.retired ? 'Retired — nothing new lands in it.' : null,
    links: {
       heading: 'Related records',
       links: [recordLink({ kind: RecordKind.FORGE, key: repository.forge_id, label: repository.forge_slug }), ...secretLinks(repository.secret_name)],
       emptyText: 'No related records.',
    },
    hasHistory: true,
  };
}

/** A repository's form fields. The slug is the handle people type and may be renamed; the
 * name is its display label, and left blank on create it takes the slug. */
export const REPOSITORY_FIELDS: readonly ConfigFieldDef<keyof RepositoryCreateRequest>[] = [
  { key: 'slug', label: 'Slug', kind: 'text', required: true },
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'forge', label: 'Forge', kind: 'select', required: true },
  { key: 'owner', label: 'Owner', kind: 'text', required: true },
  { key: 'repo', label: 'Repository', kind: 'text', required: true },
  { key: 'base_branch', label: 'Base branch', kind: 'text', required: true },
  { key: 'secret_name', label: 'Token secret', kind: 'text', required: true },
];
