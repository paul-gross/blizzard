import { lifecycleTone } from '../../core/lifecycle-tone';
import type { ConfigFieldOption } from '../config-edit.model';
import type { ConfigFieldView } from '../config-field-tree.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { credentialLinks, factText, recordKindLabel, recordRoute } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigLinkVm, ConfigRecordVm } from '../config-record-panel';
import {
  type CredentialChoice,
  type ForgeChoice,
  honourFor,
  KNOWN_LANDING_POLICIES,
  type KnownLandingPolicy,
  type LandingPolicy,
  type LandingPolicyHonour,
  landingPolicyKey,
  liveCredentials,
  liveForges,
  nameRepeatsSlug,
  parks,
  type Repository,
  type RepositoryDraft,
} from './repository.domain';

const LANDING_POLICY_LABEL: Readonly<Record<KnownLandingPolicy, string>> = {
  'merge-on-checks': 'merge on checks',
  'fast-forward': 'fast-forward',
  park: 'park',
};

/** A landing policy's name as the board shows it; an unlisted one reads raw. */
export function landingPolicyLabel(policy: LandingPolicy): string {
  return typeof policy === 'string' ? LANDING_POLICY_LABEL[policy] : policy.unlisted;
}

/**
 * The landing-policy choices. With the hub's list read, its policies in its order, each
 * disabled when the chosen forge's honour entry for it says it is not honoured — its
 * label names what is missing. Without the list, the policies this build knows; without a
 * chosen forge's honour, none disabled. The hub answers a policy it refuses.
 */
export function landingPolicyOptions(
  policies: readonly LandingPolicy[] | null,
  honour: readonly LandingPolicyHonour[] | null,
): readonly ConfigFieldOption[] {
  return (policies ?? KNOWN_LANDING_POLICIES).map((policy) => {
    const value = landingPolicyKey(policy);
    const label = landingPolicyLabel(policy);
    const entry = honour === null ? undefined : honourFor(honour, policy);
    return entry === undefined || entry.honoured
      ? { value, label }
      : { value, label: `${label} — forge lacks ${entry.missing.join(', ')}`, disabled: true };
  });
}

/** The forge choices: each live forge. */
function forgeOptions(forges: readonly ForgeChoice[]): readonly ConfigFieldOption[] {
  return liveForges(forges).map((forge) => ({ value: forge.id, label: `${forge.slug} · ${forge.name}` }));
}

/** The credential choices: each live credential, keyed by its forge, so the select offers only the picked
 * forge's credentials and a forge change clears the pick. */
function credentialOptions(credentials: readonly CredentialChoice[]): readonly ConfigFieldOption[] {
  return liveCredentials(credentials).map((credential) => ({
    value: credential.id,
    label: `${credential.slug} · ${credential.binding}`,
    kind: credential.forgeId,
  }));
}

/** The repository form's fields with its forge, credential and landing-policy choices filled in. */
export function repositoryFieldViews(
  forges: readonly ForgeChoice[],
  credentials: readonly CredentialChoice[],
  policies: readonly LandingPolicy[] | null,
  honour: readonly LandingPolicyHonour[] | null,
): readonly ConfigFieldView<RepositoryDraft>[] {
  return REPOSITORY_FIELD_VIEWS.map((field) => {
    switch (field.key) {
      case 'forge':
        return { ...field, options: forgeOptions(forges) };
      case 'credential':
        return { ...field, options: credentialOptions(credentials) };
      case 'landingPolicy':
        return { ...field, options: landingPolicyOptions(policies, honour) };
      default:
        return field;
    }
  });
}

/** The forge whose honour list a form's landing-policy choices follow: the draft's, while the form is open and names
 * one. */
export function honourForge(open: boolean, draft: Pick<RepositoryDraft, 'forge'>): string | null {
  return open && draft.forge !== '' ? draft.forge : null;
}

/** A repository's park-timeout fact — only for a repository that parks. */
function parkTimeoutFacts(repository: Repository): readonly { label: string; value: string }[] {
  if (!parks(repository.landingPolicy)) return [];
  const hours = repository.parkTimeoutHours;
  return [{ label: 'Park timeout', value: hours === null ? 'default' : `${hours} h` }];
}

/** A repository's badges — retired. */
export function repositoryBadges(repository: Repository): readonly ConfigBadgeVm[] {
  return repository.retired ? [{ label: 'retired', tone: lifecycleTone(true) }] : [];
}

/** The repository list's rows under `filter`. */
export function repositoryRows(repositories: readonly Repository[], filter: LifecycleFilter): readonly ConfigRowVm[] {
  return filterByLifecycle(repositories, filter).map((repository) => ({
    key: repository.id,
    title: repository.slug,
    sub: [
      ...(nameRepeatsSlug(repository) ? [] : [repository.name]),
      `${repository.owner}/${repository.repo}`,
      `base ${repository.baseBranch}`,
    ],
    badges: repositoryBadges(repository),
    revision: repository.revision,
    retired: repository.retired,
  }));
}

/** A link to the forge a repository lands through. */
function forgeLink(repository: Repository): ConfigLinkVm {
  return {
    kind: recordKindLabel('forge'),
    name: repository.forge.slug,
    route: recordRoute('forge', repository.forge.id),
  };
}

/** A repository's detail — `null` with none loaded. */
export function repositoryRecordVm(repository: Repository | undefined): ConfigRecordVm | null {
  if (!repository) return null;
  return {
    name: repository.slug,
    badges: repositoryBadges(repository),
    facts: [
      { label: 'Name', value: factText(repository.name) },
      { label: 'Forge', value: factText(repository.forge.name) },
      { label: 'Owner', value: factText(repository.owner) },
      { label: 'Repository', value: factText(repository.repo) },
      { label: 'Base branch', value: factText(repository.baseBranch) },
      { label: 'Landing policy', value: landingPolicyLabel(repository.landingPolicy) },
      { label: 'Allow no CI', value: repository.allowNoCi ? 'Yes' : 'No' },
      { label: 'No CI policy', value: 'Only permits absent checks, not failing or pending checks. Snapshotted at the first turn of a visit; changes affect later visits.' },
      ...parkTimeoutFacts(repository),
      { label: 'Created by', value: factText(repository.createdByName ?? repository.createdBy) },
    ],
    revision: repository.revision,
    note: repository.retired ? 'Retired — nothing new lands in it.' : null,
    links: {
      heading: 'Related records',
      links: [forgeLink(repository), ...credentialLinks(repository.credential?.id, repository.credential?.slug)],
      emptyText: 'No related records.',
    },
    hasHistory: true,
  };
}

/** A repository's form fields, presentation only. The slug is the handle people type and may be renamed; the name is
 * its display label, and left blank on create it takes the slug. The credential it acts with is one of its forge's,
 * and the park timeout, shown only for `park`, is the default or a stated number of hours. */
const REPOSITORY_FIELD_VIEWS: readonly ConfigFieldView<RepositoryDraft>[] = [
  { key: 'slug', label: 'Slug', kind: 'text' },
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'forge', label: 'Forge', kind: 'select' },
  { key: 'owner', label: 'Owner', kind: 'text' },
  { key: 'repo', label: 'Repository', kind: 'text' },
  { key: 'baseBranch', label: 'Base branch', kind: 'text' },
  { key: 'credential', label: 'Credential', kind: 'select', filterBy: 'forge' },
  { key: 'landingPolicy', label: 'Landing policy', kind: 'select' },
  { key: 'allowNoCi', label: 'Allow no CI', kind: 'checkbox' },
  {
    key: 'parkTimeoutHours',
    label: 'Park timeout (hours)',
    kind: 'number',
    orDefault: { key: 'parkTimeoutDefault', label: 'Default' },
    shownWhen: { key: 'landingPolicy', value: 'park' },
  },
];
