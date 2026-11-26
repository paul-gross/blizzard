/** How an accepted proposal closes: minting a linked work item, or agreeing and minting nothing. */
export type ProposalAcceptanceMode = 'mint' | 'decline';

/**
 * What the proposal Accept dialog edits. `body` and `urgency` concern only a mint; `reason` is one field in both modes,
 * optional for a mint and required for a decline. An empty `urgency` keeps the tenant default.
 */
export interface ProposalAcceptanceDraft {
  readonly mode: ProposalAcceptanceMode;
  readonly body: string;
  readonly reason: string;
  readonly urgency: string;
}

/** The draft an Accept dialog opens with: a mint of the proposal's own body, with no reason and the tenant default. */
export function acceptanceDraftFor(proposalBody: string): ProposalAcceptanceDraft {
  return { mode: 'mint', body: proposalBody, reason: '', urgency: '' };
}

export const DECLINE_REASON_REQUIRED = 'Required';

/** Whether the draft is a decline still owing its reason; whitespace alone counts as blank. */
export function isDeclineReasonMissing({ mode, reason }: Pick<ProposalAcceptanceDraft, 'mode' | 'reason'>): boolean {
  return mode === 'decline' && reason.trim() === '';
}
