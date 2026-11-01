import {
  type ForgeDetail,
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
import { factText, recordLink, secretLinks } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';
import { forgePickerFields } from '../forges/forges.model';

type LandingPolicyName = hubApi.LandingPolicyName;
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
    secret_name: text('secret_name'),
    ...(text('landing_policy') === '' ? {} : { landing_policy: text('landing_policy') as LandingPolicyName }),
    ...(text('park_timeout_hours') === '' ? {} : { park_timeout_hours: Number(text('park_timeout_hours')) }),
  };
}

/** A landing policy as the repositories schema lists it — the forge capabilities it
 * requires, and the ones it uses when offered. */
export interface LandingPolicyNeeds {
  readonly policy: string;
  readonly requires: readonly string[];
  readonly uses: readonly string[];
}

/** The schema extension that lists every landing policy. */
export const LANDING_POLICIES_KEY = 'x-blizzard-landing-policies';

const isStrings = (value: unknown): value is readonly string[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'string');

function isPolicyNeeds(value: unknown): value is LandingPolicyNeeds {
  if (typeof value !== 'object' || value === null) return false;
  const entry = value as Record<string, unknown>;
  return typeof entry['policy'] === 'string' && isStrings(entry['requires']) && isStrings(entry['uses']);
}

/** The landing policies a repositories schema lists, in its order — `null` when it
 * lists none or lists them malformed. */
export function landingPolicies(schema: unknown): readonly LandingPolicyNeeds[] | null {
  if (typeof schema !== 'object' || schema === null) return null;
  const list = (schema as Record<string, unknown>)[LANDING_POLICIES_KEY];
  return Array.isArray(list) && list.length > 0 && list.every(isPolicyNeeds) ? list : null;
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
 * The landing-policy choices. With the schema read, its policies in its order, each
 * disabled when the chosen forge's `offered` lacks one it requires — its label names
 * what is missing. Without the schema, the generated policies, none disabled; the hub
 * answers a policy it refuses.
 */
export function landingPolicyOptions(
  policies: readonly LandingPolicyNeeds[] | null,
  offered: readonly string[] | null,
): readonly ConfigFieldOption[] {
  if (policies === null) {
    return Object.values(LandingPolicyName).map((policy) => ({ value: policy, label: landingPolicyLabel(policy) }));
  }
  return policies.map(({ policy, requires }) => {
    const missing = offered === null ? [] : requires.filter((capability) => !offered.includes(capability));
    const label = landingPolicyLabel(policy);
    return missing.length === 0
      ? { value: policy, label }
      : { value: policy, label: `${label} — forge lacks ${missing.join(', ')}`, disabled: true };
  });
}

/** The capabilities of the forge a form has chosen — `null` with none chosen or its
 * detail not yet read. */
export function chosenForgeOffered(forgeId: string | null, forge: ForgeDetail | undefined): readonly string[] | null {
  return forgeId === null ? null : (forge?.offered ?? null);
}

/** The repository form's fields with its forge and landing-policy choices filled in. */
export function repositoryFields(
  forges: readonly ForgeSummary[],
  policies: readonly LandingPolicyNeeds[] | null,
  offered: readonly string[] | null,
): readonly ConfigFieldDef<keyof RepositoryCreateRequest>[] {
  return forgePickerFields(REPOSITORY_FIELDS, forges).map((field) =>
    field.key === 'landing_policy' ? { ...field, options: landingPolicyOptions(policies, offered) } : field,
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
      ...parkTimeoutFacts(repository),
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
  { key: 'landing_policy', label: 'Landing policy', kind: 'select' },
  {
    key: 'park_timeout_hours',
    label: 'Park timeout (hours)',
    kind: 'number',
    shownWhen: { key: 'landing_policy', value: LandingPolicyName.PARK },
  },
];
