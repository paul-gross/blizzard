import { computed, type Signal } from '@angular/core';
import { type EditBaseline, readRefusal, type SaveOutcome } from 'fleet';

import type { ForgeDraft } from './forge.domain';
import { FORGE_REFUSALS, toForgeCreateRequest, toForgeEditRequest } from './forge.translate';
import { injectCreateForgeMutation, injectEditForgeMutation } from './forges.mutations';

/** Saves a forge draft: domain in, a `SaveOutcome` out. */
export interface ForgeService {
  /** Whether a create or an edit is in flight. */
  readonly pending: Signal<boolean>;
  create(draft: ForgeDraft): Promise<SaveOutcome<ForgeDraft>>;
  /** Saves `after` against the baseline the edit started from; its revision goes as `If-Match`. */
  edit(before: EditBaseline<ForgeDraft>, after: ForgeDraft): Promise<SaveOutcome<ForgeDraft>>;
}

/**
 * The forge service over the existing create and edit mutations. A save resolves only once the mutation's
 * invalidations settle (`bzh:frontend-mutation-settles-on-refresh`). A refusal is answered, never thrown: a field
 * refusal on its field, and anything else — a revision conflict included — on the form line. Nothing is retried, and
 * no newer cached revision is substituted for the baseline's.
 */
export function injectForgeService(): ForgeService {
  const createMutation = injectCreateForgeMutation();
  const editMutation = injectEditForgeMutation();
  return {
    pending: computed(() => createMutation.isPending() || editMutation.isPending()),
    async create(draft) {
      try {
        await createMutation.mutateAsync(toForgeCreateRequest(draft));
        return { ok: true };
      } catch (error) {
        return readRefusal(error, FORGE_REFUSALS, 'Create failed.');
      }
    },
    async edit(before, after) {
      try {
        await editMutation.mutateAsync({
          forgeId: before.id,
          revision: before.revision,
          body: toForgeEditRequest(before.draft, after),
        });
        return { ok: true };
      } catch (error) {
        return readRefusal(error, FORGE_REFUSALS);
      }
    },
  };
}
