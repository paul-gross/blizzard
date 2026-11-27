import type { RefusalMap } from 'fleet/forms';

import type { FindingExitVars, FindingSupersedeVars } from '../../core/finding.mutations';
import type { FindingTriageDraft } from './finding-triage.domain';

/** A triage draft as the vars of the mutation its verb names: the trimmed note, and the trimmed absorbing id only
 * under `supersede`. */
export function toExitVars(findingIds: readonly string[], draft: FindingTriageDraft): FindingExitVars {
  return { findingIds, note: draft.note.trim() };
}

export function toSupersedeVars(findingIds: readonly string[], draft: FindingTriageDraft): FindingSupersedeVars {
  return { ...toExitVars(findingIds, draft), supersededBy: draft.supersededBy.trim() };
}

const FIELD_BY_PATH: Readonly<Record<string, keyof FindingTriageDraft>> = {
  note: 'note',
  superseded_by: 'supersededBy',
};

/** The hub's finding refusals that concern one field. Ambiguous or whole-request ones (`finding-not-found`, a
 * refused transition, contention, a duplicate id) have no field and land on the form line. */
export const FINDING_TRIAGE_REFUSALS: RefusalMap<FindingTriageDraft> = {
  field: (path) => FIELD_BY_PATH[path] ?? null,
  kinds: {
    'urn:blizzard:problem:finding-note-required': 'note',
    'urn:blizzard:problem:finding-supersedes-itself': 'supersededBy',
    'urn:blizzard:problem:absorber-not-live': 'supersededBy',
  },
};
