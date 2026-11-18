// exemplar: bzh:frontend-edit-baseline, bzh:frontend-mutation-settles-on-refresh.

import { type EditBaseline, readRefusal, type SaveOutcome } from 'fleet';

import { injectEditCadenceMutation } from './cadence-edit.mutations';
import type { CadenceDraft } from './cadence.domain';
import { CADENCE_REFUSALS, toEditRequest } from './cadence.translate';

/** Saves a cadence draft: domain in, a `SaveOutcome` out. */
export interface CadenceService {
  /** Saves `after` against the baseline the edit started from; its revision goes as `If-Match`. */
  edit(before: EditBaseline<CadenceDraft>, after: CadenceDraft): Promise<SaveOutcome<CadenceDraft>>;
}

/** The cadence service over the edit mutation. A save resolves once the mutation's invalidations settle; a refusal is
 * answered, never thrown — a revision conflict on the form line, never retried against a newer revision. */
export function injectCadenceService(): CadenceService {
  const editMutation = injectEditCadenceMutation();
  return {
    async edit(before, after) {
      try {
        await editMutation.mutateAsync({
          cadenceId: before.id,
          revision: before.revision,
          body: toEditRequest(before.draft, after),
        });
        return { ok: true };
      } catch (error) {
        return readRefusal(error, CADENCE_REFUSALS);
      }
    },
  };
}
