// bzh:frontend-domain-boundary — the new hub item as the board states it; no wire, no Angular.

/** The fields a new hub item states, as typed: `project` is the project the item lands in and `urgency` an urgency id, `''` while unchosen. */
export interface NewHubItemDraft {
  readonly title: string;
  readonly body: string;
  readonly project: string;
  readonly urgency: string;
}

export const EMPTY_NEW_HUB_ITEM_DRAFT: NewHubItemDraft = { title: '', body: '', project: '', urgency: '' };

/** The draft a new item starts from: the lens's project and the tenant's default live urgency, each `''` when there is none. */
export function startingDraft(project: string | null, urgency: string | null): NewHubItemDraft {
  return { ...EMPTY_NEW_HUB_ITEM_DRAFT, project: project ?? '', urgency: urgency ?? '' };
}
