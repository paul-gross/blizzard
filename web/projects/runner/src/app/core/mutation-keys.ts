/**
 * The TanStack Query mutation keys the local panel's mutations carry — mirrors
 * `query-keys.ts`'s own `runner`-namespaced flat-constant shape, with a `mutation`
 * segment so a mutation key is never mistaken for a query key sharing the same
 * namespace. The runner's mutations live in their own `runner` namespace, distinct
 * from `fleet`'s hub-namespaced registry, so the keys are kept here.
 */
export const chunkPauseMutationKey = ['runner', 'mutation', 'chunk-pause'] as const;

/** The runner-pauses-itself PATCH's own key (`status.query.ts`'s `injectLocalPauseMutation`)
 * — kept distinct from {@link chunkPauseMutationKey}, which pauses a *chunk*, not the runner. */
export const localPauseMutationKey = ['runner', 'mutation', 'local-pause'] as const;

export const runnerLogoutMutationKey = ['runner', 'mutation', 'runner-logout'] as const;
