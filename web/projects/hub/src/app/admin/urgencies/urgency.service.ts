import { computed, type Signal } from '@angular/core';
import { readRefusal } from 'fleet/core';
import { type EditBaseline, type SaveOutcome } from 'fleet/forms';
import { urgencyMarkError, type UrgencyDraft } from './urgency.domain';
import { toCreateRequest, toEditRequest, URGENCY_REFUSALS } from './urgency.translate';
import { injectCreateUrgencyMutation, injectEditUrgencyMutation } from './urgencies.mutations';

export interface UrgencyService {
  readonly pending: Signal<boolean>;
  create(draft: UrgencyDraft, onCreated: (id: string) => void): Promise<SaveOutcome<UrgencyDraft>>;
  edit(before: EditBaseline<UrgencyDraft>, after: UrgencyDraft): Promise<SaveOutcome<UrgencyDraft>>;
}
export function injectUrgencyService(): UrgencyService {
  const create = injectCreateUrgencyMutation(); const edit = injectEditUrgencyMutation();
  return {
    pending: computed(() => create.isPending() || edit.isPending()),
    async create(draft, onCreated) {
      const mark = urgencyMarkError(draft.mark);
      if (mark) return { ok: false, fields: { mark }, form: null };
      try { const id = await create.mutateAsync(toCreateRequest(draft)); onCreated(id); return { ok: true }; }
      catch (error) { return readRefusal(error, URGENCY_REFUSALS, 'Create failed.'); }
    },
    async edit(before, after) {
      const mark = urgencyMarkError(after.mark);
      if (mark) return { ok: false, fields: { mark }, form: null };
      try { await edit.mutateAsync({ key: before.id, revision: before.revision, body: toEditRequest(before.draft, after) }); return { ok: true }; }
      catch (error) { return readRefusal(error, URGENCY_REFUSALS); }
    },
  };
}
