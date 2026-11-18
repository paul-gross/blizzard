// The secret domain: what a stored secret is as the board reads it, and the drafts its forms edit. No wire and no
// Angular; the translator is the one place the wire is read and written (bzh:frontend-domain-boundary).

/** A record that refers to a secret, by kind and key; `label` is its current handle when it has one. */
export interface SecretReference {
  readonly kind: string;
  readonly key: string;
  readonly label: string | null;
}

/** A secret as the board reads it. Its value is write-only: the hub never serves it, so none is held here. */
export interface Secret {
  readonly name: string;
  readonly revision: number;
  readonly retired: boolean;
  readonly retirable: boolean;
  readonly replacedBy: string;
  readonly replacedByName: string | null;
  readonly references: readonly SecretReference[];
}

/** What the create form edits: the secret's name and its value, both as typed. */
export interface SecretCreateDraft {
  name: string;
  value: string;
}

/** What the replace form edits: the new value, as typed. It is never read from the secret. */
export interface SecretReplaceDraft {
  value: string;
}

/** The create form's opening draft. */
export const EMPTY_SECRET_CREATE_DRAFT: SecretCreateDraft = { name: '', value: '' };

/** The replace form's opening draft: blank, since the current value is never known. */
export const EMPTY_SECRET_REPLACE_DRAFT: SecretReplaceDraft = { value: '' };
