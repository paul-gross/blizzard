import { type Signal, computed, signal } from '@angular/core';

import { errorMessage, injectPendingMutationVariables } from 'fleet';
import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import {
  hostEnrollMutationKey,
  hostReleaseMutationKey,
  hostRetireMutationKey,
  hostRevokeTokenMutationKey,
} from '../core/mutation-keys';
import type { HostVerbEvent, HostVerbsVm } from './host-verbs.model';
import {
  HostRetireRefused,
  type HostReleaseVars,
  type HostRetireVars,
  injectHostEnrollMutation,
  injectHostReleaseMutation,
  injectHostRetireMutation,
  injectHostRevokeTokenMutation,
} from './hosts.mutations';

/**
 * The host verbs a runners container owns — release, retire, revoke-token and enroll —
 * with their pending state, the hub's refusal of an unforced retire, and the token an
 * enroll returned, folded into the {@link HostVerbsVm} the host cards render. Offered
 * only when the identity holds `runner:retire`, read off `me` as `runner:pause` is.
 *
 * `run` takes a confirmed {@link HostVerbEvent}; a failure is handed to `onError`, the
 * container's own error slot. The enrolled token lives only in this signal, cleared on
 * dismiss, and is never written to a query cache.
 */
export function injectHostVerbs(onError: (message: string | null) => void): {
  readonly vm: Signal<HostVerbsVm>;
  readonly run: (event: HostVerbEvent) => void;
} {
  const meQuery = injectMeQuery();
  const release = injectHostReleaseMutation();
  const retire = injectHostRetireMutation();
  const revokeToken = injectHostRevokeTokenMutation();
  const enroll = injectHostEnrollMutation();

  const pendingReleases = injectPendingMutationVariables<HostReleaseVars>(hostReleaseMutationKey);
  const pendingRetires = injectPendingMutationVariables<HostRetireVars>(hostRetireMutationKey);
  const pendingRevokes = injectPendingMutationVariables<string>(hostRevokeTokenMutationKey);
  const pendingEnrolls = injectPendingMutationVariables<string>(hostEnrollMutationKey);

  const refusal = signal<HostVerbsVm['refusal']>(null);
  const enrolled = signal<HostVerbsVm['enrolled']>(null);

  const vm = computed<HostVerbsVm>(() => ({
    canRetire: hasPermission(meQuery.data(), 'runner:retire'),
    pendingHostIds: [
      ...pendingRetires().map((vars) => vars.hostId),
      ...pendingRevokes(),
      ...pendingEnrolls(),
    ],
    pendingRunnerIds: pendingReleases().map((vars) => vars.runnerId),
    refusal: refusal(),
    enrolled: enrolled(),
  }));

  function run(event: HostVerbEvent): void {
    onError(null);
    refusal.set(null);
    switch (event.kind) {
      case 'dismiss':
        enrolled.set(null);
        return;
      case 'release':
        release.mutate(
          { hostId: event.hostId, runnerId: event.runnerId },
          { onError: (error) => onError(errorMessage(error, 'Release failed.')) },
        );
        return;
      case 'retire':
        retire.mutate(
          { hostId: event.hostId, force: event.force },
          {
            onError: (error) =>
              error instanceof HostRetireRefused
                ? refusal.set({ hostId: event.hostId, message: errorMessage(error.body, 'The hub refused the retire.') })
                : onError(errorMessage(error, 'Retire failed.')),
          },
        );
        return;
      case 'revoke-token':
        revokeToken.mutate(event.hostId, {
          onError: (error) => onError(errorMessage(error, 'Token revocation failed.')),
        });
        return;
      case 'enroll':
        enroll.mutate(event.hostId, {
          onSuccess: (token) => {
            enrolled.set({ hostId: event.hostId, token });
            enroll.reset();
          },
          onError: (error) => onError(errorMessage(error, 'Enroll failed.')),
        });
        return;
    }
  }

  return { vm, run };
}
