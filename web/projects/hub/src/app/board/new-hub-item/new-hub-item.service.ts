import { computed, type Signal } from '@angular/core';
import { readRefusal, type SaveOutcome } from 'fleet';

import type { NewHubItemDraft } from './new-hub-item.domain';
import { injectCreateHubItemMutation } from './new-hub-item.mutations';
import { NEW_HUB_ITEM_REFUSALS, toCreateRequest } from './new-hub-item.translate';

/** Saves a new hub item: domain in, a `SaveOutcome` out. */
export interface NewHubItemService {
  readonly pending: Signal<boolean>;
  /** Creates the item and answers once the backlog and chunk refreshes settle; `onCreated` receives the minted chunk's id. */
  create(draft: NewHubItemDraft, onCreated: (chunkId: string) => void): Promise<SaveOutcome<NewHubItemDraft>>;
}

/**
 * The new-item service over the create mutation. A refusal is answered, never thrown: a field refusal on its field,
 * anything else on the form line. Nothing is retried.
 */
export function injectNewHubItemService(): NewHubItemService {
  const mutation = injectCreateHubItemMutation();
  return {
    pending: computed(() => mutation.isPending()),
    async create(draft, onCreated) {
      let chunkId: string;
      try {
        chunkId = await mutation.mutateAsync(toCreateRequest(draft));
      } catch (error) {
        return readRefusal(error, NEW_HUB_ITEM_REFUSALS, 'Could not create hub item.');
      }
      onCreated(chunkId);
      return { ok: true };
    },
  };
}
