import type { CredentialSummary, ForgeSummary, hubApi, RepositoryCreateRequest, RepositorySummary } from 'fleet';

import { createText } from '../config-edit.model';
import {
  type CredentialChoice,
  type ForgeChoice,
  KNOWN_LANDING_POLICIES,
  type KnownLandingPolicy,
  type LandingPolicy,
  type LandingPolicyHonour,
  type Repository,
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

/** The create request the legacy repository form's values make: a name left empty is omitted, and the hub takes the slug. */
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
    ...(text('landing_policy') === '' ? {} : { landing_policy: text('landing_policy') as hubApi.LandingPolicyName }),
    ...(text('park_timeout_hours') === '' ? {} : { park_timeout_hours: Number(text('park_timeout_hours')) }),
  };
}
