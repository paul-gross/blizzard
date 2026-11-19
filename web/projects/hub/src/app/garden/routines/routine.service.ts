import { computed, type Signal } from '@angular/core';
import { readRefusal, type SaveOutcome } from 'fleet';

import type { RoutineCreateDraft } from '../core/routine.domain';
import { ROUTINE_CREATE_REFUSALS, toCreateRequest } from '../core/routine.translate';
import { injectCreateRoutineMutation } from './routine-create.mutations';

/** Saves a routine draft: domain in, a `SaveOutcome` out. */
export interface RoutineService {
  /** Whether a create is in flight. */
  readonly pending: Signal<boolean>;
  create(draft: RoutineCreateDraft): Promise<SaveOutcome<RoutineCreateDraft>>;
}

/**
 * The routine service over the create mutation. A save resolves only once the mutation's invalidations settle
 * (`bzh:frontend-mutation-settles-on-refresh`). A refusal is answered, never thrown: a field refusal on its field,
 * anything else on the form line. Create is unversioned and sends no `If-Match`.
 */
export function injectRoutineService(): RoutineService {
  const createMutation = injectCreateRoutineMutation();
  return {
    pending: computed(() => createMutation.isPending()),
    async create(draft) {
      try {
        await createMutation.mutateAsync(toCreateRequest(draft));
        return { ok: true };
      } catch (error) {
        return readRefusal(error, ROUTINE_CREATE_REFUSALS, 'Create failed.');
      }
    },
  };
}
