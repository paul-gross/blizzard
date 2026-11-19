// bzh:frontend-domain-boundary — the one place an awaiting-human draft is written as a save and its refusals read.
import type { AnswerDraft, DecisionNoteDraft, RefusalMap } from 'fleet';

import type { AnswerVars, ResolveVars } from './human.mutations';

/** An answer draft as the answer mutation's vars: the trimmed answer. */
export function toAnswerVars(chunkId: string, questionId: string, draft: AnswerDraft): AnswerVars {
  return { chunkId, questionId, answer: draft.answer.trim() };
}

/** A gate's note draft and the picked choice as the resolve mutation's vars: the trimmed note, a blank one omitted. */
export function toResolveVars(chunkId: string, decisionId: string, choice: string, draft: DecisionNoteDraft): ResolveVars {
  const note = draft.note.trim();
  return { chunkId, decisionId, choice, ...(note ? { note } : {}) };
}

/** The hub's answer refusals that concern the answer field: its validation path. Anything else — an unknown or
 * closed question — has no field and lands on the form line. */
export const ANSWER_REFUSALS: RefusalMap<AnswerDraft> = {
  field: (path) => (path === 'answer' ? 'answer' : null),
  kinds: {},
};

/** The hub's resolve refusals that concern the note field: its validation path. Anything else — an unknown or
 * closed decision, a choice the gate does not offer — has no field and lands on the form line. */
export const DECISION_NOTE_REFUSALS: RefusalMap<DecisionNoteDraft> = {
  field: (path) => (path === 'note' ? 'note' : null),
  kinds: {},
};
