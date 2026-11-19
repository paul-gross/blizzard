// bzh:frontend-domain-boundary — the one place the hub-item create wire is written and its refusals read.
import type { RefusalMap } from 'fleet';

import type { NewHubItemDraft } from './new-hub-item.domain';

const CREATE_REFUSAL_FIELDS: Readonly<Record<string, keyof NewHubItemDraft>> = {
  title: 'title',
  body: 'body',
  project: 'project',
  urgency: 'urgency',
};

/** How the hub's create refusals reach the draft: each validation entry to its own field; anything else lands on the form line. */
export const NEW_HUB_ITEM_REFUSALS: RefusalMap<NewHubItemDraft> = {
  field: (path) => (Object.hasOwn(CREATE_REFUSAL_FIELDS, path) ? CREATE_REFUSAL_FIELDS[path] : null),
  kinds: {},
};

/** The create a draft states: the title trimmed, the body as typed. */
export function toCreateRequest(draft: NewHubItemDraft) {
  return { project: draft.project, title: draft.title.trim(), body: draft.body, urgency: draft.urgency };
}
