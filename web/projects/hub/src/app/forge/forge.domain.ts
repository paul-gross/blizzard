// bzh:frontend-domain-boundary — the forge as the hub reads it and as its forms edit it; no wire, no Angular.

/** What a forge's create and edit forms hold: every field as text, blank for none. */
export interface ForgeDraft {
  readonly slug: string;
  readonly name: string;
  readonly kind: string;
  readonly apiBase: string;
  readonly webBase: string;
}

/** The draft a create opens with. */
export const EMPTY_FORGE_DRAFT: ForgeDraft = { slug: '', name: '', kind: '', apiBase: '', webBase: '' };

/** A forge as the list reads it. */
export interface Forge {
  readonly id: string;
  readonly slug: string;
  readonly name: string;
  readonly kind: string;
  readonly apiBase: string;
  readonly webBase: string | null;
  /** The web origin the hub derives from the forge's bases. */
  readonly webOrigin: string;
  readonly revision: number;
  readonly retired: boolean;
}

/** One rate-limit reading of a forge. */
export interface ForgeBudget {
  readonly bucket: string;
  readonly secretName: string | null;
  readonly remaining: number;
  readonly limit: number;
  readonly reset: string;
}

/** A repository or work source that uses a forge. */
export interface ForgeReferrer {
  readonly key: string;
  readonly label: string | null;
}

/** A forge as its detail reads it. */
export interface ForgeRecord extends Forge {
  readonly createdBy: string;
  readonly createdByName: string | null;
  readonly offered: readonly string[];
  readonly budgets: readonly ForgeBudget[];
  readonly repositories: readonly ForgeReferrer[];
  readonly workSources: readonly ForgeReferrer[];
}
