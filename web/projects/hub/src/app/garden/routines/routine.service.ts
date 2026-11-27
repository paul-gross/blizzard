import { computed, type Signal } from '@angular/core';
import { readRefusal } from 'fleet/core';
import { type EditBaseline, type SaveOutcome } from 'fleet/forms';

import type { RoutineCreateDraft, RoutineEditDraft } from '../core/routine.domain';
import { ROUTINE_CREATE_REFUSALS, routineEditRefusals, toCreateRequest, toEditRequest } from '../core/routine.translate';
import { injectCreateRoutineMutation } from './routine-create.mutations';
import { injectEditRoutineMutation } from './routine-edit.mutations';

/** Saves a routine draft: domain in, a `SaveOutcome` out. */
export interface RoutineService {
  /** Whether a create or an edit is in flight. */
  readonly pending: Signal<boolean>;
  create(draft: RoutineCreateDraft): Promise<SaveOutcome<RoutineCreateDraft>>;
  /** Saves `after` against the baseline the edit started from; its revision goes as `If-Match`. */
  edit(before: EditBaseline<RoutineEditDraft>, after: RoutineEditDraft): Promise<SaveOutcome<RoutineEditDraft>>;
}

/**
 * The routine service over the create and edit mutations. A save resolves only once the mutation's invalidations settle
 * (`bzh:frontend-mutation-settles-on-refresh`). A refusal is answered, never thrown: a field refusal on its field,
 * anything else — a revision conflict included — on the form line. Create is unversioned and sends no `If-Match`.
 * Nothing is retried, and no newer cached revision is substituted for the baseline's.
 */
export function injectRoutineService(): RoutineService {
  const createMutation = injectCreateRoutineMutation();
  const editMutation = injectEditRoutineMutation();
  return {
    pending: computed(() => createMutation.isPending() || editMutation.isPending()),
    async create(draft) {
      try {
        await createMutation.mutateAsync(toCreateRequest(draft));
        return { ok: true };
      } catch (error) {
        return readRefusal(error, ROUTINE_CREATE_REFUSALS, 'Create failed.');
      }
    },
    async edit(before, after) {
      const body = toEditRequest(before.draft, after);
      // A save that changes nothing sends nothing.
      if (Object.keys(body).length === 0) return { ok: true };
      try {
        await editMutation.mutateAsync({ routineId: before.id, revision: before.revision, body });
        return { ok: true };
      } catch (error) {
        return readRefusal(error, routineEditRefusals(before.draft, after), 'Save failed.');
      }
    },
  };
}
