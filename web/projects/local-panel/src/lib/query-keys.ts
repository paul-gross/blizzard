/**
 * The TanStack Query keys the local panel reads under, in one place — mirrors
 * `fleet`'s `hub`-namespaced `query-keys.ts` (D-097's fleet/local split: local
 * pages own their own keys). Every key is namespaced under `runner` so it can
 * never collide with a `hub`-namespaced key from the shared `fleet` library.
 */
export const runnerLeasesKey = ['runner', 'leases'] as const;
