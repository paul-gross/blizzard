import { computed, type Signal } from '@angular/core';
import { type EditBaseline, readRefusal, type SaveOutcome } from 'fleet';

import type { CadenceDraft } from '../core/cadence.domain';
import { CADENCE_REFUSALS, toCreateRequest, toEditRequest } from '../core/cadence.translate';
import { injectCreateCadenceMutation, injectEditCadenceMutation } from './cadence-edit.mutations';

/** Saves a cadence draft: domain in, a `SaveOutcome` out. */
export interface CadenceService {
  /** Whether a create or an edit is in flight. */
  readonly pending: Signal<boolean>;
  create(draft: CadenceDraft): Promise<SaveOutcome<CadenceDraft>>;
  /** Saves `after` against the baseline the edit started from; its revision goes as `If-Match`. */
  edit(before: EditBaseline<CadenceDraft>, after: CadenceDraft): Promise<SaveOutcome<CadenceDraft>>;
}

/**
 * The cadence service over the existing create and edit mutations. A save resolves only once the mutation's
 * invalidations settle (`bzh:frontend-mutation-settles-on-refresh`). A refusal is answered, never thrown: a field
 * refusal on its field, and anything else — a revision conflict included — on the form line. Nothing is retried, and
 * no newer cached revision is substituted for the baseline's.
 */
export function injectCadenceService(): CadenceService {
  const createMutation = injectCreateCadenceMutation();
  const editMutation = injectEditCadenceMutation();
  return {
    pending: computed(() => createMutation.isPending() || editMutation.isPending()),
    async create(draft) {
      try {
        await createMutation.mutateAsync(toCreateRequest(draft));
        return { ok: true };
      } catch (error) {
        return readRefusal(error, CADENCE_REFUSALS, 'Create failed.');
      }
    },
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
