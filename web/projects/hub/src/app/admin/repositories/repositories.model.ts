import type { RepositorySummary } from 'fleet';

import type { ConfigFieldDef } from '../config-edit.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { factText, secretLinks } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';

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
    key: repository.name,
    title: repository.name,
    sub: [`${repository.owner}/${repository.repo}`, `base ${repository.base_branch}`],
    badges: repositoryBadges(repository),
    revision: repository.revision,
    retired: repository.retired ?? false,
  }));
}

/** A repository's detail — `null` with none loaded. */
export function repositoryRecordVm(repository: RepositorySummary | undefined): ConfigRecordVm | null {
  if (!repository) return null;
  return {
    name: repository.name,
    badges: repositoryBadges(repository),
    facts: [
      { label: 'Forge API', value: factText(repository.forge_api_url) },
      { label: 'Owner', value: factText(repository.owner) },
      { label: 'Repository', value: factText(repository.repo) },
      { label: 'Base branch', value: factText(repository.base_branch) },
      { label: 'Created by', value: factText(repository.created_by) },
    ],
    revision: repository.revision,
    note: repository.retired ? 'Retired — nothing new lands in it.' : null,
    links: {
      heading: 'Token',
      links: secretLinks(repository.secret_name),
      emptyText: 'No token secret.',
    },
    hasHistory: true,
  };
}

/** A repository's form fields. The name is set on create alone. */
export const REPOSITORY_FIELDS: readonly ConfigFieldDef[] = [
  {
    key: 'name',
    label: 'Name',
    kind: 'text',
    required: true,
    createOnly: true,
  },
  { key: 'forge_api_url', label: 'Forge API', kind: 'text', required: true },
  { key: 'owner', label: 'Owner', kind: 'text', required: true },
  { key: 'repo', label: 'Repository', kind: 'text', required: true },
  { key: 'base_branch', label: 'Base branch', kind: 'text', required: true },
  { key: 'secret_name', label: 'Token secret', kind: 'text', required: true },
];
