import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  enrollHostApiHostsHostIdEnrollmentsPost,
  hubHostsKey,
  hubRunnersKey,
  retireHostApiHostsHostIdRetirePost,
  revokeHostTokenApiHostsHostIdTokenRevocationsPost,
} from 'fleet';
import {
  hostEnrollMutationKey,
  hostRetireMutationKey,
  hostRevokeTokenMutationKey,
} from '../core/mutation-keys';

/** Retire a host; `force` releases the chunks its runners hold instead of being refused. */
export interface HostRetireVars {
  readonly hostId: string;
  readonly force: boolean;
}

/** The hub's refusal of an unforced retire — its runners hold chunks — carrying the hub's
 * own error body so the caller shows its words in the forced confirm. */
export class HostRetireRefused extends Error {
  constructor(readonly body: unknown) {
    super('host retire refused');
  }
}

/** Every host verb changes both the hosts read and the runners read under it. */
function settleHostReads(queryClient: QueryClient): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: hubHostsKey }),
    queryClient.invalidateQueries({ queryKey: hubRunnersKey }),
  ]);
}

/** `POST /api/hosts/{id}/retire`. An unforced retire the hub refuses with a 409 — its
 * runners hold chunks — throws {@link HostRetireRefused}, so the caller can ask again
 * with `force`. */
export function injectHostRetireMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: hostRetireMutationKey,
    mutationFn: async (vars: HostRetireVars): Promise<void> => {
      const { error, response } = await retireHostApiHostsHostIdRetirePost({
        path: { host_id: vars.hostId },
        body: { by: 'operator', force: vars.force },
        throwOnError: false,
      });
      if (error) throw !vars.force && response?.status === 409 ? new HostRetireRefused(error) : error;
    },
    onSettled: () => settleHostReads(queryClient),
  }));
}

/** `POST /api/hosts/{id}/token-revocations` — the host stays added, with no token, until
 * enrolled afresh. */
export function injectHostRevokeTokenMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: hostRevokeTokenMutationKey,
    mutationFn: async (hostId: string): Promise<void> => {
      const { error } = await revokeHostTokenApiHostsHostIdTokenRevocationsPost({
        path: { host_id: hostId },
        body: { by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => settleHostReads(queryClient),
  }));
}

/** `POST /api/hosts/{id}/enrollments` — issues the host a fresh token, returned once. The
 * mutation keeps nothing past its own call (`gcTime: 0`); the caller holds the token only
 * while it is on screen. */
export function injectHostEnrollMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: hostEnrollMutationKey,
    gcTime: 0,
    mutationFn: async (hostId: string): Promise<string> => {
      const { data, error } = await enrollHostApiHostsHostIdEnrollmentsPost({
        path: { host_id: hostId },
        throwOnError: false,
      });
      if (error) throw error;
      return data!.token;
    },
    onSettled: () => settleHostReads(queryClient),
  }));
}
