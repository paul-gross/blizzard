import { type Provider, computed, signal } from '@angular/core';
import { type AnswerQuestionEvent, type ChunkPageActions, type EditGraphEvent, type ResolveDecisionEvent, errorMessage, hubClient, injectPendingMutationVariables, provideChunkPageDaemon } from 'fleet';
import { type AnswerVars, injectAnswerQuestionMutation, injectResolveDecisionMutation, readAnswerFailure } from '../chunks/human.mutations';
import { answerQuestionMutationKey } from '../../mutation-keys';
import { hasPermission, injectMeQuery } from '../../auth/me.query';
import { injectSetChunkGraphMutation } from '../chunks/edit.mutations';

/** The graphs view's own path segments — the target every hub composition site links a
 * graph badge to. */
const GRAPH_LINK_BASE: readonly string[] = ['/graphs'];

/**
 * The hub's operator-action port for the shared chunk page — its `/api/me` permissions
 * and the three human-loop mutations (answer, resolve, set graph) the desktop dock writes
 * through. Called from the page's own injection context, so every visit gets fresh
 * notice channels.
 *
 * The two report channels stay separate for the dock's reason ("report, don't swallow"):
 * a 404/409/422 must read as a failure rather than a tap that appears to do nothing, and a
 * lost first-write-wins answer race — the phone being the surface most likely to lose one
 * — reads as an outcome naming the winner, not a failure; `readAnswerFailure` owns that
 * fold so this page and the dock cannot drift. Both clear at the start of every action, so
 * a stale outcome never sits beside an unrelated failure.
 */
export function injectHubChunkActions(): ChunkPageActions {
  const meQuery = injectMeQuery();
  const answerMutation = injectAnswerQuestionMutation();
  const pendingAnswers = injectPendingMutationVariables<AnswerVars>(answerQuestionMutationKey);
  const resolveMutation = injectResolveDecisionMutation();
  const editGraphMutation = injectSetChunkGraphMutation();
  const actionError = signal<string | null>(null);
  const actionOutcome = signal<string | null>(null);

  function beginAction(): void {
    actionError.set(null);
    actionOutcome.set(null);
  }

  return {
    // `null`/pending identity resolves every permission to `false` — hidden until confirmed.
    canControl: computed(() => hasPermission(meQuery.data(), 'chunk:control')),
    canAnswer: computed(() => hasPermission(meQuery.data(), 'question:answer')),
    canResolve: computed(() => hasPermission(meQuery.data(), 'gate:resolve')),
    canReadTranscripts: computed(() => hasPermission(meQuery.data(), 'transcript:read')),
    resolvePending: computed(() => resolveMutation.isPending()),
    pendingAnswerQuestionIds: computed(() => pendingAnswers().map((vars) => vars.questionId)),
    actionError: actionError.asReadonly(),
    actionOutcome: actionOutcome.asReadonly(),
    graphLinkBase: GRAPH_LINK_BASE,
    answer(event: AnswerQuestionEvent): void {
      beginAction();
      answerMutation.mutate(
        { questionId: event.questionId, answer: event.answer, chunkId: event.chunkId },
        {
          onError: (error) => {
            const failure = readAnswerFailure(error);
            if (failure.kind === 'outcome') actionOutcome.set(failure.message);
            else actionError.set(failure.message);
          },
        },
      );
    },
    resolve(event: ResolveDecisionEvent): void {
      beginAction();
      resolveMutation.mutate(
        { decisionId: event.decisionId, choice: event.choice, chunkId: event.chunkId, struck: event.struck },
        { onError: (error) => actionError.set(errorMessage(error, 'Resolve failed.')) },
      );
    },
    editGraph(event: EditGraphEvent): void {
      beginAction();
      editGraphMutation.mutate(
        { chunkId: event.chunkId, graphId: event.graphId },
        { onError: (error) => actionError.set(errorMessage(error, 'Set graph failed.')) },
      );
    },
  };
}

/** The hub's daemon for the shared chunk page — its own client and plane, and the
 * operator-action port above. */
export const HUB_CHUNK_PAGE_PROVIDERS: Provider[] = [
  provideChunkPageDaemon({ client: hubClient, plane: 'hub', actions: injectHubChunkActions }),
];
