import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type AnswerResult,
  type DecisionResolutionResponse,
  answerQuestionApiQuestionsQuestionIdAnswerPost,
  resolveDecisionApiDecisionsDecisionIdResolutionPost,
} from '../api/hub';
import { hubChunkKey, hubChunksKey } from '../query-keys';

/** Answer a chunk's open question — the board's counterpart of `blizzard hub answer` (D-052). */
export interface AnswerVars {
  readonly questionId: string;
  readonly answer: string;
  /** The chunk this question parks, so the detail re-reads on success. */
  readonly chunkId: string;
}

/**
 * `POST /api/questions/{id}/answer` — first-write-wins CAS answer through the
 * generated client (bzh:generated-client). On success it re-reads the parked chunk's
 * detail and the fleet list (the answer flips it out of `waiting_on_human`).
 */
export function injectAnswerQuestionMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: AnswerVars): Promise<AnswerResult> => {
      const { data, error } = await answerQuestionApiQuestionsQuestionIdAnswerPost({
        path: { question_id: vars.questionId },
        body: { answer: vars.answer, answered_by: 'operator' },
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

/** Resolve a chunk's open gate decision — the board's choice buttons (D-042/D-052). */
export interface ResolveVars {
  readonly decisionId: string;
  readonly choice: string;
  /** The chunk this decision parks, so the detail re-reads on success. */
  readonly chunkId: string;
}

/**
 * `POST /api/decisions/{id}/resolution` — a person picks one choice, first-write-wins
 * CAS (D-045), through the generated client (bzh:generated-client). The holding runner
 * records the resolving transition over its pull; here we re-read the chunk and the list.
 */
export function injectResolveDecisionMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ResolveVars): Promise<DecisionResolutionResponse> => {
      const { data, error } = await resolveDecisionApiDecisionsDecisionIdResolutionPost({
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
