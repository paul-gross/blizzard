import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type AnswerResult,
  type DecisionResolutionResponse,
  answerQuestionApiQuestionsQuestionIdAnswersPost,
  resolveDecisionApiDecisionsDecisionIdResolutionsPost,
} from '../api/hub';
import { hubChunkKey, hubChunksKey } from '../query-keys';

/** Answer a chunk's open question — the board's counterpart of `blizzard hub answer`. */
export interface AnswerVars {
  readonly questionId: string;
  readonly answer: string;
  /** The chunk this question parks, so the detail re-reads on success. */
  readonly chunkId: string;
}

/**
 * Read a losing answer's 409 body — the winning {@link AnswerResult} (issue #165).
 *
 * The hub's first-write-wins arbitration answers a beaten writer with the *winning row*,
 * not an error message: `{won: false, answer, answered_by, …}` and no `detail` field at
 * all. That is why this exists rather than the shared `errorMessage()` fold, which reads
 * only `detail` and so turned the one response carrying real news into a generic
 * "Answer failed." Returns `null` for anything that is not that shape, so a genuine
 * transport or 404 failure still falls through to the error path.
 */
export function readAnswerConflict(error: unknown): AnswerResult | null {
  if (!error || typeof error !== 'object') return null;
  const body = error as Partial<AnswerResult>;
  return body.won === false && typeof body.answer === 'string' && typeof body.answered_by === 'string'
    ? (body as AnswerResult)
    : null;
}

/**
 * `POST /api/questions/{id}/answers` — first-write-wins CAS answer through the
 * generated client (bzh:generated-client); `POST /api/questions/{id}/answer` is now
 * a deprecated alias this board no longer calls.
 *
 * Re-reads the parked chunk's detail and the fleet list on `onSettled`, not `onSuccess`:
 * a **lost** race (the 409 {@link readAnswerConflict} reads) changed the server state
 * just as much as a won one — someone else's answer landed — so the dock must re-read to
 * show the question as answered with the winner's trail rather than sitting on the stale
 * open row (issue #165).
 */
export function injectAnswerQuestionMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: AnswerVars): Promise<AnswerResult> => {
      const { data, error } = await answerQuestionApiQuestionsQuestionIdAnswersPost({
        path: { question_id: vars.questionId },
        body: { answer: vars.answer, answered_by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: (_data, _error, vars) => {
      void queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) });
      void queryClient.invalidateQueries({ queryKey: hubChunksKey });
    },
  }));
}

/** Resolve a chunk's open gate decision — the board's choice buttons. */
export interface ResolveVars {
  readonly decisionId: string;
  readonly choice: string;
  /** The chunk this decision parks, so the detail re-reads on success. */
  readonly chunkId: string;
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
    mutationFn: async (vars: ResolveVars): Promise<DecisionResolutionResponse> => {
      const { data, error } = await resolveDecisionApiDecisionsDecisionIdResolutionsPost({
        path: { decision_id: vars.decisionId },
        body: { choice: vars.choice, resolved_by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: (_data, vars) => {
      void queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) });
      void queryClient.invalidateQueries({ queryKey: hubChunksKey });
    },
  }));
}
