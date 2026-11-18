/** What the proposal Pass dialog edits: the reason the proposal is passed, as typed. */
export interface ProposalPassDraft {
  reason: string;
}

export function emptyPassDraft(): ProposalPassDraft {
  return { reason: '' };
}
