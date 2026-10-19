import { type Provider, computed, inject, signal } from '@angular/core';
import { type AnswerQuestionEvent, type ChunkPageActions, type DaemonTarget, type ResolveDecisionEvent, FLEET_CLOCK, hubClient, injectPendingMutationVariables, provideChunkPageDaemon } from 'fleet';
import { type AnswerVars, injectAnswerQuestionMutation, injectResolveDecisionMutation, readAnswerFailure, readDecisionFailure } from '../chunks/human.mutations';
import { answerQuestionMutationKey } from '../../core/mutation-keys';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';

/** The graphs view's own path segments — the target every hub composition site links a
 * graph badge to. */
const GRAPH_LINK_BASE: readonly string[] = ['/graphs'];

/** The events view's own path segments — the chunk page links a chunk's own events
 * there, filtered by `?chunk=`. */
const EVENTS_LINK_BASE: readonly string[] = ['/events'];

/**
 * The hub's operator-action port for the shared chunk page — its `/api/me` permissions
 * and the two human-loop mutations (answer, resolve). Called from the page's
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
  const actionError = signal<string | null>(null);
  const actionOutcome = signal<string | null>(null);

  function beginAction(): void {
    actionError.set(null);
    actionOutcome.set(null);
  }

  return {
    // `null`/pending identity resolves every permission to `false` — hidden until confirmed.
    canAnswer: computed(() => hasPermission(meQuery.data(), 'question:answer')),
    canResolve: computed(() => hasPermission(meQuery.data(), 'gate:resolve')),
    canReadTranscripts: computed(() => hasPermission(meQuery.data(), 'transcript:read')),
    resolvePending: computed(() => resolveMutation.isPending()),
    pendingAnswerQuestionIds: computed(() => pendingAnswers().map((vars) => vars.questionId)),
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
        { decisionId: event.decisionId, choice: event.choice, chunkId: event.chunkId },
        {
          onError: (error) => {
            const failure = readDecisionFailure(error, new Date(clock()));
            if (failure.kind === 'outcome') actionOutcome.set(failure.message);
            else actionError.set(failure.message);
          },
        },
      );
    },
  };
}

/** The board view's own path segments — the chunk page links back there, and its chunk
 * links sit under `/board/chunk`. */
const BOARD_LINK_BASE: readonly string[] = ['/board'];

/** The hub's own target for the shared chunk page's reads. */
const HUB_TARGET: DaemonTarget = { plane: 'hub', client: hubClient };

/** The hub's daemon for the shared chunk page — its own target and board, and the
 * operator-action port above. */
export const HUB_CHUNK_PAGE_PROVIDERS: Provider[] = [
  provideChunkPageDaemon(() => ({
    target: () => HUB_TARGET,
    boardLinkBase: () => BOARD_LINK_BASE,
    actions: injectHubChunkActions,
  })),
];
