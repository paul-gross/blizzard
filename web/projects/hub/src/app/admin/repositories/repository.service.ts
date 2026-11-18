import { computed, type Signal } from '@angular/core';

import { type EditBaseline, readRefusal, type SaveOutcome } from 'fleet';
import { injectCreateRepositoryMutation, injectEditRepositoryMutation } from './repositories.mutations';
import type { RepositoryDraft } from './repository.domain';
import { REPOSITORY_REFUSALS, toCreateRequest, toEditRequest } from './repository.translate';

/** Saves a repository draft: domain in, a `SaveOutcome` out. */
export interface RepositoryService {
  /** Whether a create or an edit is in flight. */
  readonly pending: Signal<boolean>;
  create(draft: RepositoryDraft): Promise<SaveOutcome<RepositoryDraft>>;
  /** Saves `after` against the baseline the edit started from; its revision goes as `If-Match`. */
  edit(before: EditBaseline<RepositoryDraft>, after: RepositoryDraft): Promise<SaveOutcome<RepositoryDraft>>;
}

/**
 * The repository service over the create and edit mutations. A draft that does not translate — stated park-timeout
 * hours that are not whole hours from 1 — is answered as its field error and sends nothing, with or without a form in
 * front of it. A save resolves once the mutation's invalidations settle; a refusal is answered, never thrown — a
 * revision conflict on the form line, never retried against a newer revision.
 */
export function injectRepositoryService(): RepositoryService {
  const createMutation = injectCreateRepositoryMutation();
  const editMutation = injectEditRepositoryMutation();
  return {
    pending: computed(() => createMutation.isPending() || editMutation.isPending()),
    async create(draft) {
      const translated = toCreateRequest(draft);
      if (!translated.ok) return { ok: false, fields: translated.fields, form: null };
      try {
        await createMutation.mutateAsync(translated.request);
        return { ok: true };
      } catch (error) {
        return readRefusal(error, REPOSITORY_REFUSALS, 'Create failed.');
      }
    },
    async edit(before, after) {
      const translated = toEditRequest(before.draft, after);
      if (!translated.ok) return { ok: false, fields: translated.fields, form: null };
      try {
        await editMutation.mutateAsync({ repositoryId: before.id, revision: before.revision, body: translated.request });
        return { ok: true };
      } catch (error) {
        return readRefusal(error, REPOSITORY_REFUSALS);
      }
    },
  };
}
