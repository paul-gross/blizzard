import { computed } from '@angular/core';
import { type ChunkControl, errorMessage, injectPendingMutationVariables, isPendingFor } from 'fleet';

import { injectCompleteChunkMutation, type CompleteVars } from './complete.mutations';
import { type DeleteVars, injectDeleteChunkMutation } from './delete.mutations';
import { type DetachVars, injectDetachChunkMutation } from './detach.mutations';
import { injectChunkPauseMutation, type ChunkPauseVars } from './pause.mutations';
import { chunkCompleteMutationKey, chunkDeleteMutationKey, chunkDetachMutationKey, chunkPauseMutationKey } from '../../core/mutation-keys';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';

/** Where a hub chunk-control port reports — the host's own notice channels. */
export interface HubChunkControlReporting {
  /** Clear the host's notice channels; every action starts here. */
  readonly beginAction: () => void;
  /** Report a failed action's message. */
  readonly fail: (message: string) => void;
  /** Run with the chunk id as soon as the hub confirms a delete, before the lists
   * re-read — the dock closes there, ahead of any per-call `onDeleted`. */
  readonly onDeleted?: (chunkId: string) => void;
}

/**
 * The hub's chunk-control port behind a chunk header's ⋮ menu (`ChunkActionsMenu`) — the
 * Pause/Resume, Detach, Complete, and Delete mutations, `chunk:control` from `/api/me`,
 * and each action's in-flight read scoped to one chunk id. The board dock and the
 * routed chunk page both wire their menu through it. Called from the host's injection
 * context.
 */
export function injectHubChunkControl(reporting: HubChunkControlReporting): ChunkControl {
  const meQuery = injectMeQuery();
  const detachMutation = injectDetachChunkMutation();
  const pauseMutation = injectChunkPauseMutation();
  const completeMutation = injectCompleteChunkMutation();
  const deleteMutation = injectDeleteChunkMutation(reporting.onDeleted);
  const pendingPauses = injectPendingMutationVariables<ChunkPauseVars>(chunkPauseMutationKey);
  const pendingDetaches = injectPendingMutationVariables<DetachVars>(chunkDetachMutationKey);
  const pendingCompletes = injectPendingMutationVariables<CompleteVars>(chunkCompleteMutationKey);
  const pendingDeletes = injectPendingMutationVariables<DeleteVars>(chunkDeleteMutationKey);
  const onError = (fallback: string) => ({ onError: (error: unknown) => reporting.fail(errorMessage(error, fallback)) });
  return {
    // `null`/pending identity resolves to `false` — the menu stays hidden until confirmed.
    canControl: computed(() => hasPermission(meQuery.data(), 'chunk:control')),
    pausePending: (chunkId) => isPendingFor(pendingPauses(), (v) => v.chunkId === chunkId),
    detachPending: (chunkId) => isPendingFor(pendingDetaches(), (v) => v.chunkId === chunkId),
    completePending: (chunkId) => isPendingFor(pendingCompletes(), (v) => v.chunkId === chunkId),
    deletePending: (chunkId) => isPendingFor(pendingDeletes(), (v) => v.chunkId === chunkId),
    pause(chunkId) {
      reporting.beginAction();
      pauseMutation.mutate({ chunkId, paused: true }, onError('Pause failed.'));
    },
    resume(chunkId) {
      reporting.beginAction();
      pauseMutation.mutate({ chunkId, paused: false }, onError('Resume failed.'));
    },
    detach(chunkId) {
      reporting.beginAction();
      detachMutation.mutate({ chunkId }, onError('Detach failed.'));
    },
    complete(chunkId) {
      reporting.beginAction();
      completeMutation.mutate({ chunkId }, onError('Complete failed.'));
    },
    delete(chunkId, onDeleted) {
      reporting.beginAction();
      deleteMutation.mutate({ chunkId }, { onSuccess: () => onDeleted?.(), ...onError('Delete failed.') });
    },
  };
}
