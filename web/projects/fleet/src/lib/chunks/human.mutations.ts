import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type AnswerResult,
  type DecisionResolutionResponse,
  answerQuestionApiQuestionsQuestionIdAnswersPost,
  resolveDecisionApiDecisionsDecisionIdResolutionsPost,
} from '../api/hub';
import { errorMessage } from '../error-message';
import { answerQuestionMutationKey, resolveDecisionMutationKey } from '../mutation-keys';
import { hubChunkKey, hubChunksKey } from '../query-keys';

/** Answer a chunk's open question — the board's counterpart of `blizzard hub answer`. */
export interface AnswerVars {
  readonly questionId: string;
  readonly answer: string;
  /** The chunk this question parks, so the detail re-reads on success. */
  readonly chunkId: string;
}

/**
 * Read a losing answer's 409 body — the winning {@link AnswerResult}, which carries no
 * `detail` field — or `null` for any other failure. Pinned by `chunk-detail.spec.ts`'s
 * "renders the winner’s name and answer as an outcome when the answer race is lost" and
 * `chunk-page.spec.ts`'s "renders a lost answer race as an outcome naming the winner, not
 * "Answer failed."".
 */
function readAnswerConflict(error: unknown): AnswerResult | null {
  if (!error || typeof error !== 'object') return null;
  const body = error as Partial<AnswerResult>;
  return body.won === false && typeof body.answer === 'string' && typeof body.answered_by === 'string'
    ? (body as AnswerResult)
    : null;
}

/**
 * How a failed answer should read to the operator: `outcome` for a lost first-write-wins
 * race — news, not a failure — and `error` for everything else.
 */
export interface AnswerFailure {
  readonly kind: 'outcome' | 'error';
  readonly message: string;
}

/**
 * Fold an answer mutation's `onError` into the channel it belongs on, for both surfaces
 * that answer a question. Pinned per surface by `chunk-detail.spec.ts`'s "renders the
 * winner’s name and answer as an outcome when the answer race is lost" and
 * `chunk-page.spec.ts`'s "renders a lost answer race as an outcome naming the winner, not
 * "Answer failed."".
 */
export function readAnswerFailure(error: unknown): AnswerFailure {
  const winner = readAnswerConflict(error);
  return winner
    ? { kind: 'outcome', message: `${winner.answered_by} answered first: “${winner.answer}”` }
    : { kind: 'error', message: errorMessage(error, 'Answer failed.') };
}

/**
 * `POST /api/questions/{id}/answers` — first-write-wins CAS answer through the
 * generated client (bzh:generated-client); `POST /api/questions/{id}/answer` is now
 * a deprecated alias this board no longer calls.
 *
 * Re-reads the parked chunk's detail and the fleet list on `onSettled`, not `onSuccess`:
 * a **lost** race (the 409 {@link readAnswerFailure} folds) changed the server state just
 * as much as a won one — someone else's answer landed — so the board must re-read to show
 * the question as answered with the winner's trail rather than sitting on the stale open
 * row.
 */
export function injectAnswerQuestionMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: answerQuestionMutationKey,
    mutationFn: async (vars: AnswerVars): Promise<AnswerResult> => {
      const { data, error } = await answerQuestionApiQuestionsQuestionIdAnswersPost({
        path: { question_id: vars.questionId },
        body: { answer: vars.answer, answered_by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) }),
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
      ]),
  }));
}

/** Resolve a chunk's open gate decision — the board's choice buttons. */
export interface ResolveVars {
  readonly decisionId: string;
  readonly choice: string;
  /** The chunk this decision parks, so the detail re-reads on success. */
  readonly chunkId: string;
  /** The docket proposals to strike — empty passes every proposal. */
  readonly struck: readonly string[];
}

/**
 * `POST /api/decisions/{id}/resolutions` — a person picks one choice, first-write-wins
 * CAS, through the generated client (bzh:generated-client); `POST
 * /api/decisions/{id}/resolution` is now a deprecated alias this board no longer
 * calls. The holding runner records the resolving transition over its pull; here we
 * re-read the chunk and the list.
 */
export function injectResolveDecisionMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: resolveDecisionMutationKey,
    mutationFn: async (vars: ResolveVars): Promise<DecisionResolutionResponse> => {
      const { data, error } = await resolveDecisionApiDecisionsDecisionIdResolutionsPost({
        path: { decision_id: vars.decisionId },
        body: { choice: vars.choice, resolved_by: 'operator', struck: [...vars.struck] },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) }),
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
      ]),
  }));
}
