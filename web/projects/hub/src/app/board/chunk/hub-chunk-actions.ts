import { type Provider, computed, inject, signal } from '@angular/core';
import { type AnswerQuestionEvent, type ChunkPageActions, type EditGraphEvent, type ResolveDecisionEvent, FLEET_CLOCK, errorMessage, hubClient, injectPendingMutationVariables, provideChunkPageDaemon } from 'fleet';
import { type AnswerVars, injectAnswerQuestionMutation, injectResolveDecisionMutation, readAnswerFailure, readDecisionFailure } from '../chunks/human.mutations';
import { answerQuestionMutationKey, chunkSetGraphMutationKey } from '../../core/mutation-keys';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { type ChunkGraphEditVars, injectSetChunkGraphMutation } from '../chunks/edit.mutations';

/** The graphs view's own path segments — the target every hub composition site links a
 * graph badge to. */
const GRAPH_LINK_BASE: readonly string[] = ['/graphs'];

/** The events view's own path segments — the chunk page links a chunk's own events
 * there, filtered by `?chunk=`. */
const EVENTS_LINK_BASE: readonly string[] = ['/events'];

/**
 * The hub's operator-action port for the shared chunk page — its `/api/me` permissions
 * and the three human-loop mutations (answer, resolve, set graph). Called from the page's
 * own injection context, so every visit gets fresh notice channels. Failures and
 * outcomes are folded by `readAnswerFailure` and `readDecisionFailure`; both channels
 * clear at the start of every action, so a stale outcome never sits beside an unrelated
 * failure.
 */
export function injectHubChunkActions(): ChunkPageActions {
  const meQuery = injectMeQuery();
  const clock = inject(FLEET_CLOCK);
  const answerMutation = injectAnswerQuestionMutation();
  const pendingAnswers = injectPendingMutationVariables<AnswerVars>(answerQuestionMutationKey);
  const resolveMutation = injectResolveDecisionMutation();
  const editGraphMutation = injectSetChunkGraphMutation();
  const pendingGraphEdits = injectPendingMutationVariables<ChunkGraphEditVars>(chunkSetGraphMutationKey);
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
    pendingGraphChunkIds: computed(() => pendingGraphEdits().map((vars) => vars.chunkId)),
    actionError: actionError.asReadonly(),
    actionOutcome: actionOutcome.asReadonly(),
    graphLinkBase: GRAPH_LINK_BASE,
    eventsLinkBase: EVENTS_LINK_BASE,
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
        {
          onError: (error) => {
            const failure = readDecisionFailure(error, new Date(clock()));
            if (failure.kind === 'outcome') actionOutcome.set(failure.message);
            else actionError.set(failure.message);
          },
        },
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
