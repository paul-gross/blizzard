import { computed, type Signal } from '@angular/core';
import { readRefusal } from 'fleet/core';
import { type EditBaseline, type SaveOutcome } from 'fleet/forms';

import type { WorkSourceDraft } from './work-source.domain';
import { toCreateRequest, toEditRequest, WORK_SOURCE_REFUSALS } from './work-source.translate';
import { injectCreateWorkSourceMutation, injectEditWorkSourceMutation } from './work-sources.mutations';

/** Saves a work-source draft: domain in, a `SaveOutcome` out. */
export interface WorkSourceService {
  /** Whether a create or an edit is in flight. */
  readonly pending: Signal<boolean>;
  create(draft: WorkSourceDraft): Promise<SaveOutcome<WorkSourceDraft>>;
  /** Saves `after` against the baseline the edit started from; its revision goes as `If-Match`. */
  edit(before: EditBaseline<WorkSourceDraft>, after: WorkSourceDraft): Promise<SaveOutcome<WorkSourceDraft>>;
}

/**
 * The work-source service over the existing create and edit mutations. A save resolves only once the mutation's
 * invalidations settle (`bzh:frontend-mutation-settles-on-refresh`). A refusal is answered, never thrown: a field
 * refusal on its field, and anything else — a revision conflict included — on the form line. Nothing is retried, and
 * no newer cached revision is substituted for the baseline's.
 */
export function injectWorkSourceService(): WorkSourceService {
  const createMutation = injectCreateWorkSourceMutation();
  const editMutation = injectEditWorkSourceMutation();
  return {
    pending: computed(() => createMutation.isPending() || editMutation.isPending()),
    async create(draft) {
      try {
        await createMutation.mutateAsync(toCreateRequest(draft));
        return { ok: true };
      } catch (error) {
        return readRefusal(error, WORK_SOURCE_REFUSALS, 'Create failed.');
      }
    },
    async edit(before, after) {
      try {
        await editMutation.mutateAsync({
          workSourceId: before.id,
          revision: before.revision,
          body: toEditRequest(before.draft, after),
        });
        return { ok: true };
      } catch (error) {
        return readRefusal(error, WORK_SOURCE_REFUSALS);
      }
    },
  };
}
