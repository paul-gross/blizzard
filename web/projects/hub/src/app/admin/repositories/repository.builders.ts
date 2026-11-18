import type { CredentialChoice, ForgeChoice, LandingPolicyHonour, Repository, RepositoryDraft } from './repository.domain';

/** A live `blizzard` repository on the GitHub forge, acting with `gh-blizzard`, merging on checks, at revision 2. */
export function repository(overrides: Partial<Repository> = {}): Repository {
  return {
    id: 'repo_1',
    slug: 'blizzard',
    name: 'blizzard',
    forge: { id: 'frg_github', slug: 'github', name: 'GitHub' },
    credential: { id: 'cred_1', slug: 'gh-blizzard' },
    owner: 'paul-gross',
    repo: 'blizzard',
    baseBranch: 'master',
    landingPolicy: 'merge-on-checks',
    allowNoCi: false,
    parkTimeoutHours: null,
    retired: false,
    revision: 2,
    createdBy: 'pgross',
    createdByName: null,
    ...overrides,
  };
}

/** A live GitHub forge choice. */
export function forgeChoice(overrides: Partial<ForgeChoice> = {}): ForgeChoice {
  return { id: 'frg_github', slug: 'github', name: 'GitHub', retired: false, ...overrides };
}

/** A live `github.token` credential choice on the GitHub forge. */
export function credentialChoice(overrides: Partial<CredentialChoice> = {}): CredentialChoice {
  return { id: 'cred_1', slug: 'gh-blizzard', binding: 'github.token', forgeId: 'frg_github', retired: false, ...overrides };
}

/** A forge's honour entry for a policy — honoured unless it names something missing. */
export function policyHonour(
  policy: LandingPolicyHonour['policy'],
  missing: readonly string[] = [],
): LandingPolicyHonour {
  return { policy, honoured: missing.length === 0, missing };
}

/** A complete create draft for `blizzard` on the GitHub forge, merging on checks, the park timeout left at its default. */
export function repositoryDraft(overrides: Partial<RepositoryDraft> = {}): RepositoryDraft {
  return {
    slug: 'blizzard',
    name: '',
    forge: 'frg_github',
    owner: 'paul-gross',
    repo: 'blizzard',
    baseBranch: 'master',
    credential: 'cred_1',
    landingPolicy: 'merge-on-checks',
    allowNoCi: false,
    parkTimeoutDefault: true,
    parkTimeoutHours: '',
    ...overrides,
  };
}
