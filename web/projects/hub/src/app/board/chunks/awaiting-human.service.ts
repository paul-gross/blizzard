import { inject } from '@angular/core';
import { type AwaitingHumanFormActions } from 'fleet/awaiting-human';
import { FLEET_CLOCK, readRefusal } from 'fleet/core';

import { ANSWER_REFUSALS, DECISION_NOTE_REFUSALS, toAnswerVars, toResolveVars } from './awaiting-human.translate';
import { injectAnswerQuestionMutation, injectResolveDecisionMutation, readAnswerFailure, readDecisionFailure } from './human.mutations';

/** The notice channels a composition site reports through beside the forms. */
export interface AwaitingHumanNotices {
  /** Clears the site's last failure and outcome; every save starts here. */
  readonly begin: () => void;
  /** Says a non-failure result that still needs saying — a lost race, naming the winner. */
  readonly outcome: (message: string) => void;
}

/**
 * The hub's awaiting-human saves over the answer and resolve mutations. A save resolves once the mutation's
 * invalidations settle (`bzh:frontend-mutation-settles-on-refresh`). A lost race closed the question or gate anyway,
 * so it goes to the site's outcome notice and answers ok; every other refusal is answered on the form, never thrown,
 * and nothing is retried.
 */
export function injectAwaitingHumanService(notices: AwaitingHumanNotices): AwaitingHumanFormActions {
  const clock = inject(FLEET_CLOCK);
  const answerMutation = injectAnswerQuestionMutation();
  const resolveMutation = injectResolveDecisionMutation();
  return {
    async answer(chunkId, questionId, draft) {
      notices.begin();
      try {
        await answerMutation.mutateAsync(toAnswerVars(chunkId, questionId, draft));
        return { ok: true };
      } catch (error) {
        const failure = readAnswerFailure(error);
        if (failure.kind === 'outcome') {
          notices.outcome(failure.message);
          return { ok: true };
        }
        return readRefusal(error, ANSWER_REFUSALS, 'Answer failed.');
      }
    },
    async resolve(chunkId, decisionId, choice, draft) {
      notices.begin();
      try {
        await resolveMutation.mutateAsync(toResolveVars(chunkId, decisionId, choice, draft));
        return { ok: true };
      } catch (error) {
        const failure = readDecisionFailure(error, new Date(clock()));
        if (failure.kind === 'outcome') {
          notices.outcome(failure.message);
          return { ok: true };
        }
        return readRefusal(error, DECISION_NOTE_REFUSALS, 'Resolve failed.');
      }
    },
  };
}
