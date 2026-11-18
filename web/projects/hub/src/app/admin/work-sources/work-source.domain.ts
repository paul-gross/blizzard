// The work-source domain: what a configured work source is, and the draft its form edits. No wire and no Angular; the
// translator is the one place the wire is read and written (bzh:frontend-domain-boundary).

/** A work source as the board reads it. The built-in `hub` source has no id, revision, or slug. */
export interface WorkSource {
  readonly id: string | null;
  readonly revision: number | null;
  readonly slug: string | null;
  readonly name: string;
  readonly provider: string | null;
  readonly locator: string | null;
  readonly forgeId: string | null;
  readonly forgeSlug: string | null;
  readonly forgeName: string | null;
  readonly credentialId: string | null;
  readonly credentialSlug: string | null;
  readonly annotate: boolean;
  readonly edit: boolean;
  readonly builtIn: boolean;
  readonly retired: boolean;
  readonly createdBy: string | null;
  readonly createdByName: string | null;
}

/** What the work-source form edits: text as typed, the forge and credential as record ids ('' for none). */
export interface WorkSourceDraft {
  slug: string;
  name: string;
  provider: string;
  locator: string;
  forge: string;
  credential: string;
  annotate: boolean;
}

/** The form's opening draft for a new work source. */
export const EMPTY_WORK_SOURCE_DRAFT: WorkSourceDraft = {
  slug: '',
  name: '',
  provider: '',
  locator: '',
  forge: '',
  credential: '',
  annotate: false,
};
