import {
  type CredentialSummary,
  type ForgeSummary,
  hubApi,
  openLookup,
  RecordKind,
  type RepositoryCreateRequest,
  type RepositorySummary,
} from 'fleet';

import { lifecycleTone } from '../../core/lifecycle-tone';
import { type ConfigFieldDef, type ConfigFieldOption, createText } from '../config-edit.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { credentialLinks, factText, recordLink } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';
import { credentialPickerFields } from '../credentials/credentials.model';
import { forgePickerFields } from '../forges/forges.model';

type LandingPolicyName = hubApi.LandingPolicyName;
type LandingPolicyNeedsView = hubApi.LandingPolicyNeedsView;
type PolicyHonour = hubApi.PolicyHonour;
const { LandingPolicyName } = hubApi;

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
    credential: text('credential'),
    ...(typeof body['allow_no_ci'] === 'boolean' ? { allow_no_ci: body['allow_no_ci'] } : {}),
    ...(text('landing_policy') === '' ? {} : { landing_policy: text('landing_policy') as LandingPolicyName }),
    ...(text('park_timeout_hours') === '' ? {} : { park_timeout_hours: Number(text('park_timeout_hours')) }),
  };
}

/** A landing policy's name as the board shows it; an unknown one reads raw. */
export const landingPolicyLabel = openLookup<LandingPolicyName, string>(
  {
    'merge-on-checks': 'merge on checks',
    'fast-forward': 'fast-forward',
    park: 'park',
  },
  (raw) => raw,
);

/**
 * The landing-policy choices. With the needs read, its policies in its order, each
 * disabled when the chosen forge's honour entry for it says `honoured: false` — its
 * label names what is `missing`. Without the needs, the generated policies; without a
 * chosen forge's honour, none disabled. The hub answers a policy it refuses.
 */
export function landingPolicyOptions(
  needs: readonly LandingPolicyNeedsView[] | null,
  honour: readonly PolicyHonour[] | null,
): readonly ConfigFieldOption[] {
  const policies: readonly string[] = needs === null ? Object.values(LandingPolicyName) : needs.map(({ policy }) => policy);
  return policies.map((policy) => {
    const label = landingPolicyLabel(policy);
    const entry = honour?.find((candidate) => candidate.policy === policy);
    return entry === undefined || entry.honoured
      ? { value: policy, label }
      : { value: policy, label: `${label} — forge lacks ${entry.missing.join(', ')}`, disabled: true };
  });
}

/** The repository form's fields with its forge, credential and landing-policy choices filled in. */
export function repositoryFields(
  forges: readonly ForgeSummary[],
  credentials: readonly CredentialSummary[],
  needs: readonly LandingPolicyNeedsView[] | null,
  honour: readonly PolicyHonour[] | null,
): readonly ConfigFieldDef<keyof RepositoryCreateRequest>[] {
  return credentialPickerFields(forgePickerFields(REPOSITORY_FIELDS, forges), credentials).map((field) =>
    field.key === 'landing_policy' ? { ...field, options: landingPolicyOptions(needs, honour) } : field,
  );
}

/** A repository's park-timeout fact — only for a repository that parks. */
function parkTimeoutFacts(repository: RepositorySummary): readonly { label: string; value: string }[] {
  if (repository.landing_policy !== LandingPolicyName.PARK) return [];
  const hours = repository.park_timeout_hours;
  return [{ label: 'Park timeout', value: hours === null ? 'default' : `${hours} h` }];
}

/** A repository's badges — retired. */
export function repositoryBadges(repository: RepositorySummary): readonly ConfigBadgeVm[] {
  return repository.retired ? [{ label: 'retired', tone: lifecycleTone(true) }] : [];
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
      { label: 'Landing policy', value: landingPolicyLabel(repository.landing_policy) },
      { label: 'Allow no CI', value: repository.allow_no_ci ? 'Yes' : 'No' },
      { label: 'No CI policy', value: 'Only permits absent checks, not failing or pending checks. Snapshotted at the first turn of a visit; changes affect later visits.' },
      ...parkTimeoutFacts(repository),
      { label: 'Created by', value: factText(repository.created_by_name ?? repository.created_by) },
    ],
    revision: repository.revision,
    note: repository.retired ? 'Retired — nothing new lands in it.' : null,
    links: {
      heading: 'Related records',
      links: [recordLink({ kind: RecordKind.FORGE, key: repository.forge_id, label: repository.forge_slug }), ...credentialLinks(repository.credential_id, repository.credential_slug)],
      emptyText: 'No related records.',
    },
    hasHistory: true,
  };
}

/** A repository's form fields. The slug is the handle people type and may be renamed; the
 * name is its display label, and left blank on create it takes the slug. The credential it acts
 * with is one of its forge's. */
export const REPOSITORY_FIELDS: readonly ConfigFieldDef<keyof RepositoryCreateRequest>[] = [
  { key: 'slug', label: 'Slug', kind: 'text', required: true },
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'forge', label: 'Forge', kind: 'select', required: true },
  { key: 'owner', label: 'Owner', kind: 'text', required: true },
  { key: 'repo', label: 'Repository', kind: 'text', required: true },
  { key: 'base_branch', label: 'Base branch', kind: 'text', required: true },
  { key: 'credential', label: 'Credential', kind: 'select', required: true, filterBy: 'forge' },
  { key: 'landing_policy', label: 'Landing policy', kind: 'select' },
  { key: 'allow_no_ci', label: 'Allow no CI', kind: 'checkbox' },
  {
    key: 'park_timeout_hours',
    label: 'Park timeout (hours)',
    kind: 'number',
    shownWhen: { key: 'landing_policy', value: LandingPolicyName.PARK },
  },
];
