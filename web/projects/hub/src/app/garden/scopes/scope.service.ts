import { computed, type Signal } from '@angular/core';
import { readRefusal, type SaveOutcome } from 'fleet';

import type { ScopeDescriptionDraft } from '../core/scope.domain';
import { SCOPE_DESCRIPTION_REFUSALS, toScopeDescriptionRequest } from '../core/scope.translate';
import { injectEditScopeMutation } from './scope-edit.mutations';

/** How a description edit ended: saved, or a refusal answered per draft field. */
export type ScopeEditOutcome = SaveOutcome<ScopeDescriptionDraft>;

/** Edits a scope's description from a draft: domain in, a {@link ScopeEditOutcome} out. */
export interface ScopeService {
  /** Whether an edit is in flight. */
  readonly pending: Signal<boolean>;
  editDescription(slug: string, draft: ScopeDescriptionDraft): Promise<ScopeEditOutcome>;
}

/**
 * The scope service over the edit mutation. An edit resolves once the mutation's invalidations settle
 * (`bzh:frontend-mutation-settles-on-refresh`); a refusal is answered, never thrown: a field refusal on its field,
 * anything else on the form line. Nothing is retried.
 */
export function injectScopeService(): ScopeService {
  const mutation = injectEditScopeMutation();
  return {
    pending: computed(() => mutation.isPending()),
    async editDescription(slug, draft) {
      try {
        await mutation.mutateAsync({ slug, ...toScopeDescriptionRequest(draft) });
        return { ok: true };
      } catch (error) {
        return readRefusal(error, SCOPE_DESCRIPTION_REFUSALS, 'Set description failed.');
      }
    },
  };
}
