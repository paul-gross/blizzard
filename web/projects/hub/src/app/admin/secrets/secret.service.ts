import { computed, type Signal } from '@angular/core';
import { type EditBaseline, readRefusal, type SaveOutcome } from 'fleet';

import type { SecretCreateDraft, SecretReplaceDraft } from './secret.domain';
import { SECRET_REFUSALS, toCreateRequest } from './secret.translate';
import { injectCreateSecretMutation, injectReplaceSecretMutation } from './secrets.mutations';

/** Saves a secret draft: domain in, a `SaveOutcome` out. */
export interface SecretService {
  /** Whether a create or a replacement is in flight. */
  readonly pending: Signal<boolean>;
  create(draft: SecretCreateDraft): Promise<SaveOutcome<SecretCreateDraft>>;
  /** Replaces the value against the baseline the replacement started from; its revision goes as `If-Match`. */
  replace(before: EditBaseline<SecretReplaceDraft>, after: SecretReplaceDraft): Promise<SaveOutcome<SecretReplaceDraft>>;
}

/**
 * The secret service over the existing create and replace mutations. A save resolves only once the mutation's
 * invalidations settle (`bzh:frontend-mutation-settles-on-refresh`). A refusal is answered, never thrown: a field
 * refusal on its field, and anything else — a revision conflict included — on the form line. Nothing is retried, and
 * the typed value stays with the caller. Retire and enable stay unversioned and go through their own mutation.
 */
export function injectSecretService(): SecretService {
  const createMutation = injectCreateSecretMutation();
  const replaceMutation = injectReplaceSecretMutation();
  return {
    pending: computed(() => createMutation.isPending() || replaceMutation.isPending()),
    async create(draft) {
      try {
        await createMutation.mutateAsync(toCreateRequest(draft));
        return { ok: true };
      } catch (error) {
        return readRefusal(error, SECRET_REFUSALS, 'Create failed.');
      }
    },
    async replace(before, after) {
      try {
        await replaceMutation.mutateAsync({
          name: before.id,
          revision: before.revision,
          value: after.value,
        });
        return { ok: true };
      } catch (error) {
        return readRefusal(error, SECRET_REFUSALS, 'Replace failed.');
      }
    },
  };
}
