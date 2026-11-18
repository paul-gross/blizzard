import type { FindingTriageVerb } from '../../core/finding-triage-verb.domain';

/**
 * What the finding-triage dialog edits. The verb is fixed for the dialog's whole lifetime, chosen before it opened;
 * the note and the absorbing finding's id are free text as typed. `supersededBy` is judged and sent only under
 * `supersede`.
 */
export interface FindingTriageDraft {
  readonly verb: FindingTriageVerb;
  note: string;
  supersededBy: string;
}

export function emptyTriageDraft(verb: FindingTriageVerb): FindingTriageDraft {
  return { verb, note: '', supersededBy: '' };
}
