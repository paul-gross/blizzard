/** The landing policies this build knows, in the hub's declared order. */
export const KNOWN_LANDING_POLICIES = ['merge-on-checks', 'fast-forward', 'park'] as const;

/** A landing policy this build can name. */
export type KnownLandingPolicy = (typeof KNOWN_LANDING_POLICIES)[number];

/** A landing policy the hub reports that this build does not list, kept raw so it reads and round-trips unchanged. */
export interface UnlistedLandingPolicy {
  readonly unlisted: string;
}

/** A repository's landing policy: an open vocabulary (`bzh:frontend-open-vocabulary`). */
export type LandingPolicy = KnownLandingPolicy | UnlistedLandingPolicy;

/** The forge a repository lands through. */
export interface RepositoryForgeRef {
  readonly id: string;
  readonly slug: string;
  readonly name: string;
}

/** The credential a repository acts with. */
export interface RepositoryCredentialRef {
  readonly id: string;
  readonly slug: string | null;
}

/** A repository, as the board reads it. */
export interface Repository {
  readonly id: string;
  /** The handle people type; it may be renamed. */
  readonly slug: string;
  /** The display label; it takes the slug when none is given. */
  readonly name: string;
  readonly forge: RepositoryForgeRef;
  readonly credential: RepositoryCredentialRef | null;
  readonly owner: string;
  readonly repo: string;
  readonly baseBranch: string;
  readonly landingPolicy: LandingPolicy;
  readonly allowNoCi: boolean;
  /** Hours a parked change waits; `null` when the hub's default applies. */
  readonly parkTimeoutHours: number | null;
  readonly retired: boolean;
  readonly revision: number;
  readonly createdBy: string;
  readonly createdByName: string | null;
}

/** A forge the repository form may offer. */
export interface ForgeChoice {
  readonly id: string;
  readonly slug: string;
  readonly name: string;
  readonly retired: boolean;
}

/** A credential the repository form may offer; it belongs to one forge. */
export interface CredentialChoice {
  readonly id: string;
  readonly slug: string;
  readonly binding: string;
  readonly forgeId: string;
  readonly retired: boolean;
}

/** Whether a forge can honour a landing policy, and what it lacks when it cannot. */
export interface LandingPolicyHonour {
  readonly policy: LandingPolicy;
  readonly honoured: boolean;
  readonly missing: readonly string[];
}

/** A landing policy's wire key — the known name, or the unlisted value as the hub sent it. */
export function landingPolicyKey(policy: LandingPolicy): string {
  return typeof policy === 'string' ? policy : policy.unlisted;
}

/** Whether `policy` parks — the only policy a park timeout applies to. */
export function parks(policy: LandingPolicy): boolean {
  return policy === 'park';
}

/** Whether a repository's name only repeats its slug. */
export function nameRepeatsSlug(repository: Repository): boolean {
  return repository.name === repository.slug;
}

/** The honour entry for `policy` in `honour`, or `undefined` when the forge's honour list does not name it. */
export function honourFor(
  honour: readonly LandingPolicyHonour[],
  policy: LandingPolicy,
): LandingPolicyHonour | undefined {
  const key = landingPolicyKey(policy);
  return honour.find((entry) => landingPolicyKey(entry.policy) === key);
}

/** The forges a form may still pick: the live ones. */
export function liveForges(forges: readonly ForgeChoice[]): readonly ForgeChoice[] {
  return forges.filter((forge) => !forge.retired);
}

/** The credentials a form may still pick: the live ones. */
export function liveCredentials(credentials: readonly CredentialChoice[]): readonly CredentialChoice[] {
  return credentials.filter((credential) => !credential.retired);
}
