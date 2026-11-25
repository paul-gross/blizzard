/**
 * The TanStack Query mutation keys the panel's mutations carry — mirrors
 * `query-keys.ts`: a runner's own mutations lead with its runner id, the host's with
 * `auth`, and each carries a `mutation` segment so a mutation key is never mistaken for a
 * query key sharing the same prefix.
 */
export function chunkPauseMutationKey(runnerId: string | null): readonly unknown[] {
  return [runnerId, 'mutation', 'chunk-pause'];
}

/** The runner-pauses-itself PATCH's own key (`status.query.ts`'s `injectLocalPauseMutation`)
 * — kept distinct from {@link chunkPauseMutationKey}, which pauses a *chunk*, not the runner. */
export function localPauseMutationKey(runnerId: string | null): readonly unknown[] {
  return [runnerId, 'mutation', 'local-pause'];
}

export function floorClearMutationKey(runnerId: string | null): readonly unknown[] {
  return [runnerId, 'mutation', 'floor-clear'];
}

export const runnerLogoutMutationKey = ['auth', 'mutation', 'runner-logout'] as const;

export function chunkRequeueMutationKey(runnerId: string | null): readonly unknown[] {
  return [runnerId, 'mutation', 'chunk-requeue'];
}

export function selftestStartMutationKey(runnerId: string | null): readonly unknown[] {
  return [runnerId, 'mutation', 'selftest-start'];
}

/** A write to the host's own settings record — `PATCH /api/host/settings`. */
export const hostSettingsWriteMutationKey = ['host', 'mutation', 'settings-write'] as const;

/** A write to one runner's settings record — `PATCH /runners/{runner_id}/api/settings`. */
export function runnerSettingsWriteMutationKey(runnerId: string): readonly unknown[] {
  return [runnerId, 'mutation', 'settings-write'];
}

/** One verb on a host record — `create`, `edit`, `retire`, or `enable` on `/api/host/workspaces` or
 * `/api/host/subscriptions`. */
export function hostRecordMutationKey(recordKind: 'workspace' | 'subscription', verb: 'create' | 'edit' | 'retire' | 'enable'): readonly unknown[] {
  return ['host', 'mutation', recordKind, verb];
}
