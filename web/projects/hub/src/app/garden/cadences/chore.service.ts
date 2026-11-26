import { computed, type Signal } from '@angular/core';
import { readRefusal } from 'fleet/core';
import { type EditBaseline, type SaveOutcome } from 'fleet/forms';

import type { Chore, ChoreDraft } from '../core/cadence.domain';
import { choreRefusals, choresAdding, choresRemoving, choresReplacing } from '../core/cadence.translate';
import { type CadenceEditVars, injectEditCadenceMutation } from './cadence-edit.mutations';

/** What a chore save starts from: the cadence's chores as one read held them, and the revision of that read. */
export type ChoresBaseline = EditBaseline<readonly Chore[]>;

/** Saves a chore add, edit, or remove: domain in, a `SaveOutcome` out. */
export interface ChoreService {
  /** Whether an edit of a cadence is in flight. */
  readonly pending: Signal<boolean>;
  add(before: ChoresBaseline, draft: ChoreDraft): Promise<SaveOutcome<ChoreDraft>>;
  /** Replaces the chore keyed `key`, which keeps that key; every other chore is resent as read. */
  edit(before: ChoresBaseline, key: string, draft: ChoreDraft): Promise<SaveOutcome<ChoreDraft>>;
  remove(before: ChoresBaseline, key: string): Promise<SaveOutcome<ChoreDraft>>;
}

/**
 * The chore service over the cadence edit mutation. Every method builds the whole chore list from the captured chores
 * only and sends the captured revision as `If-Match` — a newer read is never consulted, so a concurrent edit is
 * refused, not merged. A save resolves once the mutation's invalidations settle
 * (`bzh:frontend-mutation-settles-on-refresh`); a refusal is answered, never thrown, and nothing is retried.
 */
export function injectChoreService(): ChoreService {
  const mutation = injectEditCadenceMutation();

  async function send(
    before: ChoresBaseline,
    chores: CadenceEditVars['body']['chores'],
    index: number,
    fallback: string,
  ): Promise<SaveOutcome<ChoreDraft>> {
    try {
      await mutation.mutateAsync({ cadenceId: before.id, revision: before.revision, body: { chores } });
      return { ok: true };
    } catch (error) {
      return readRefusal(error, choreRefusals(index), fallback);
    }
  }

  return {
    pending: computed(() => mutation.isPending()),
    add: (before, draft) => send(before, choresAdding(before.draft, draft), before.draft.length, 'Add chore failed.'),
    edit: (before, key, draft) =>
      send(
        before,
        choresReplacing(before.draft, key, draft),
        before.draft.findIndex((c) => c.key === key),
        'Save chore failed.',
      ),
    remove: (before, key) =>
      send(before, choresRemoving(before.draft, key), before.draft.findIndex((c) => c.key === key), 'Remove chore failed.'),
  };
}
