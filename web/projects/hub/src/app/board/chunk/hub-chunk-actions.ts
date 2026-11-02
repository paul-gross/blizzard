import { type Provider, computed, inject, signal } from '@angular/core';
import { type AnswerQuestionEvent, type ChunkPageActions, type ChunkPageControl, type DaemonTarget, type ResolveDecisionEvent, errorMessage, FLEET_CLOCK, hubClient, injectPendingMutationVariables, isPendingFor, provideChunkPageDaemon } from 'fleet';
import { injectCompleteChunkMutation, type CompleteVars } from '../chunks/complete.mutations';
import { type DeleteVars, injectDeleteChunkMutation } from '../chunks/delete.mutations';
import { type DetachVars, injectDetachChunkMutation } from '../chunks/detach.mutations';
import { injectChunkPauseMutation, type ChunkPauseVars } from '../chunks/pause.mutations';
import { type AnswerVars, injectAnswerQuestionMutation, injectResolveDecisionMutation, readAnswerFailure, readDecisionFailure } from '../chunks/human.mutations';
import { answerQuestionMutationKey, chunkCompleteMutationKey, chunkDeleteMutationKey, chunkDetachMutationKey, chunkPauseMutationKey } from '../../core/mutation-keys';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectProjectsQuery } from '../../core/projects.query';
import { injectShowProjectColors } from '../../core/project-lens-colors';

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
    control: injectHubChunkControl(meQuery, beginAction, (message) => actionError.set(message)),
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
        { decisionId: event.decisionId, choice: event.choice, chunkId: event.chunkId, note: event.note },
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

/** The hub's chunk-control port for the chunk page header's ⋮ menu — the same four
 * mutations the board dock fires, failures reported on the page's error channel. */
function injectHubChunkControl(
  meQuery: ReturnType<typeof injectMeQuery>,
  beginAction: () => void,
  fail: (message: string) => void,
): ChunkPageControl {
  const detachMutation = injectDetachChunkMutation();
  const pauseMutation = injectChunkPauseMutation();
  const completeMutation = injectCompleteChunkMutation();
  const deleteMutation = injectDeleteChunkMutation();
  const pendingPauses = injectPendingMutationVariables<ChunkPauseVars>(chunkPauseMutationKey);
  const pendingDetaches = injectPendingMutationVariables<DetachVars>(chunkDetachMutationKey);
  const pendingCompletes = injectPendingMutationVariables<CompleteVars>(chunkCompleteMutationKey);
  const pendingDeletes = injectPendingMutationVariables<DeleteVars>(chunkDeleteMutationKey);
  const onError = (fallback: string) => ({ onError: (error: unknown) => fail(errorMessage(error, fallback)) });
  return {
    canControl: computed(() => hasPermission(meQuery.data(), 'chunk:control')),
    pausePending: (chunkId) => isPendingFor(pendingPauses(), (v) => v.chunkId === chunkId),
    detachPending: (chunkId) => isPendingFor(pendingDetaches(), (v) => v.chunkId === chunkId),
    completePending: (chunkId) => isPendingFor(pendingCompletes(), (v) => v.chunkId === chunkId),
    deletePending: (chunkId) => isPendingFor(pendingDeletes(), (v) => v.chunkId === chunkId),
    pause(chunkId) {
      beginAction();
      pauseMutation.mutate({ chunkId, paused: true }, onError('Pause failed.'));
    },
    resume(chunkId) {
      beginAction();
      pauseMutation.mutate({ chunkId, paused: false }, onError('Resume failed.'));
    },
    detach(chunkId) {
      beginAction();
      detachMutation.mutate({ chunkId }, onError('Detach failed.'));
    },
    complete(chunkId) {
      beginAction();
      completeMutation.mutate({ chunkId }, onError('Complete failed.'));
    },
    delete(chunkId, onDeleted) {
      beginAction();
      deleteMutation.mutate({ chunkId }, { onSuccess: onDeleted, ...onError('Delete failed.') });
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
  provideChunkPageDaemon(() => {
    const projects = injectProjectsQuery();
    const showProjectColors = injectShowProjectColors();
    return {
      target: () => HUB_TARGET,
      boardLinkBase: () => BOARD_LINK_BASE,
      actions: injectHubChunkActions,
      projectColor: (projectId: string) =>
        showProjectColors() ? (projects.data()?.find((p) => p.project_id === projectId)?.color ?? null) : null,
    };
  }),
];
