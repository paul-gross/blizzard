import type { EditBaseline, RefusalMap } from 'fleet/forms';
import type {
  CredentialSummary,
  ForgeSummary,
  hubApi,
  RepositoryCreateRequest,
  RepositoryPatchRequest,
  RepositorySummary,
} from 'fleet/hub-api';

import {
  type CredentialChoice,
  draftParks,
  type ForgeChoice,
  KNOWN_LANDING_POLICIES,
  type KnownLandingPolicy,
  type LandingPolicy,
  type LandingPolicyHonour,
  landingPolicyKey,
  PARK_TIMEOUT_MESSAGE,
  readParkTimeoutHours,
  type Repository,
  type RepositoryDraft,
} from './repository.domain';

/** A wire landing policy as the domain's open vocabulary: a value this build does not list is kept raw. */
export function fromLandingPolicyName(raw: string): LandingPolicy {
  return (KNOWN_LANDING_POLICIES as readonly string[]).includes(raw) ? (raw as KnownLandingPolicy) : { unlisted: raw };
}

/** A repository read as the board's domain. */
export function fromRepositorySummary(wire: RepositorySummary): Repository {
  return {
    id: wire.repository_id,
    slug: wire.slug,
    name: wire.name,
    forge: { id: wire.forge_id, slug: wire.forge_slug, name: wire.forge_name },
    credential: wire.credential_id ? { id: wire.credential_id, slug: wire.credential_slug } : null,
    owner: wire.owner,
    repo: wire.repo,
    baseBranch: wire.base_branch,
    landingPolicy: fromLandingPolicyName(wire.landing_policy),
    allowNoCi: wire.allow_no_ci,
    parkTimeoutHours: wire.park_timeout_hours,
    retired: wire.retired,
    revision: wire.revision,
    createdBy: wire.created_by,
    createdByName: wire.created_by_name,
  };
}

/** A forge as a repository form's choice. */
export function fromForgeSummary(wire: ForgeSummary): ForgeChoice {
  return { id: wire.forge_id, slug: wire.slug, name: wire.name, retired: wire.retired ?? false };
}

/** A credential as a repository form's choice. */
export function fromCredentialSummary(wire: CredentialSummary): CredentialChoice {
  return {
    id: wire.credential_id,
    slug: wire.slug,
    binding: wire.binding,
    forgeId: wire.forge_id,
    retired: wire.retired ?? false,
  };
}

/** The hub's landing-policy list, in its order, as the policies a form may offer. */
export function fromLandingPolicyNeeds(wire: readonly hubApi.LandingPolicyNeedsView[]): readonly LandingPolicy[] {
  return wire.map(({ policy }) => fromLandingPolicyName(policy));
}

/** A forge's honour entry for one landing policy. */
export function fromPolicyHonour(wire: hubApi.PolicyHonour): LandingPolicyHonour {
  return { policy: fromLandingPolicyName(wire.policy), honoured: wire.honoured, missing: wire.missing };
}

/** A repository as the draft its edit starts from. A timeout the hub leaves unset opens as the default choice. */
export function toDraft(repository: Repository): RepositoryDraft {
  return {
    slug: repository.slug,
    name: repository.name,
    forge: repository.forge.id,
    owner: repository.owner,
    repo: repository.repo,
    baseBranch: repository.baseBranch,
    credential: repository.credential?.id ?? '',
    landingPolicy: landingPolicyKey(repository.landingPolicy),
    allowNoCi: repository.allowNoCi,
    parkTimeoutDefault: repository.parkTimeoutHours === null,
    parkTimeoutHours: repository.parkTimeoutHours === null ? '' : String(repository.parkTimeoutHours),
  };
}

/** What an edit of `repository` starts from: its id, the revision it was read at, and its draft, all from that one read. */
export function toEditBaseline(repository: Repository): EditBaseline<RepositoryDraft> {
  return { id: repository.id, revision: repository.revision, draft: toDraft(repository) };
}

/** A draft as a request, or the field errors that stop it from being one. */
export type RepositoryRequest<R> =
  | { readonly ok: true; readonly request: R }
  | { readonly ok: false; readonly fields: Partial<Record<keyof RepositoryDraft, string>> };

const HOURS_REFUSED = { ok: false, fields: { parkTimeoutHours: PARK_TIMEOUT_MESSAGE } } as const;

/** The park timeout a parking draft states: `null` for the explicit default, the hours, or `undefined` when the text
 * is not a whole number of at least one hour. */
function parkTimeout(draft: RepositoryDraft): number | null | undefined {
  if (draft.parkTimeoutDefault) return null;
  return readParkTimeoutHours(draft.parkTimeoutHours) ?? undefined;
}

/** A policy key as the wire's name; an unlisted key goes as the hub sent it. */
function policyName(key: string): hubApi.LandingPolicyName {
  return key as hubApi.LandingPolicyName;
}

/** The create request for `draft`. A blank name and a blank landing policy are omitted; Allow no CI is always sent.
 * The park timeout goes only for `park`: `null` for the default choice, or the stated hours — text that is not whole
 * hours from 1 answers a field error and no request. */
export function toCreateRequest(draft: RepositoryDraft): RepositoryRequest<RepositoryCreateRequest> {
  const timeout = draftParks(draft) ? parkTimeout(draft) : null;
  if (timeout === undefined) return HOURS_REFUSED;
  const name = draft.name.trim();
  const policy = draft.landingPolicy;
  return {
    ok: true,
    request: {
      slug: draft.slug.trim(),
      ...(name === '' ? {} : { name }),
      forge: draft.forge,
      owner: draft.owner.trim(),
      repo: draft.repo.trim(),
      base_branch: draft.baseBranch.trim(),
      credential: draft.credential,
      allow_no_ci: draft.allowNoCi,
      ...(policy === '' ? {} : { landing_policy: policyName(policy) }),
      ...(draftParks(draft) ? { park_timeout_hours: timeout } : {}),
    },
  };
}

const TEXT_FIELDS = [
  ['slug', 'slug'],
  ['name', 'name'],
  ['owner', 'owner'],
  ['repo', 'repo'],
  ['baseBranch', 'base_branch'],
] as const;

/** The edit body for `after` against the draft it started from: only the fields that changed, text trimmed. The park
 * timeout is judged and sent only while `after` parks — `null` for a newly chosen default, the hours when they
 * changed by value — and stated hours that are not whole hours from 1 answer a field error and no request. */
export function toEditRequest(before: RepositoryDraft, after: RepositoryDraft): RepositoryRequest<RepositoryPatchRequest> {
  const body: RepositoryPatchRequest = {};
  for (const [key, wire] of TEXT_FIELDS) {
    const text = after[key].trim();
    if (text !== before[key].trim()) body[wire] = text;
  }
  if (after.forge !== before.forge) body.forge = after.forge;
  if (after.credential !== before.credential) body.credential = after.credential;
  if (after.landingPolicy !== before.landingPolicy && after.landingPolicy !== '') {
    body.landing_policy = policyName(after.landingPolicy);
  }
  if (after.allowNoCi !== before.allowNoCi) body.allow_no_ci = after.allowNoCi;
  if (draftParks(after)) {
    const timeout = parkTimeout(after);
    if (timeout === undefined) return HOURS_REFUSED;
    const previous = before.parkTimeoutDefault ? null : readParkTimeoutHours(before.parkTimeoutHours);
    if (timeout !== previous) body.park_timeout_hours = timeout;
  }
  return { ok: true, request: body };
}

const REFUSAL_FIELDS: Readonly<Record<string, keyof RepositoryDraft>> = {
  slug: 'slug',
  name: 'name',
  forge: 'forge',
  owner: 'owner',
  repo: 'repo',
  base_branch: 'baseBranch',
  credential: 'credential',
  landing_policy: 'landingPolicy',
  allow_no_ci: 'allowNoCi',
  park_timeout_hours: 'parkTimeoutHours',
};

/** How the hub's repository refusals reach the draft: a field path to its field, a taken slug to the slug and a taken
 * owner/repo pair to the repository; anything else, a revision conflict included, to the form line. */
export const REPOSITORY_REFUSALS: RefusalMap<RepositoryDraft> = {
  field: (path) => (Object.hasOwn(REFUSAL_FIELDS, path) ? REFUSAL_FIELDS[path] : null),
  kinds: {
    'urn:blizzard:problem:repository-slug-taken': 'slug',
    'urn:blizzard:problem:repository-coordinate-taken': 'repo',
  },
};
