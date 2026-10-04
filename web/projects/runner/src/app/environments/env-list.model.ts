import { ageMs, compactRef, formatHeldFor, type runnerApi } from 'fleet';

import type { EnvRow } from './env-list-view';

/**
 * `42m` since the binding fact — browser-clock decoration only
 * (`bzh:utc-instants` via `ageMs`): a skew-broken timestamp renders `—`, and an
 * unheld environment (no `held_since`) renders blank rather than `—`.
 */
export function heldForLabel(heldSince: string | null | undefined, now: number): string {
  if (heldSince == null) return '';
  const age = ageMs(heldSince, now);
  return age === null ? '—' : formatHeldFor(age);
}

/** One {@link EnvRow} per pool environment, in wire order, aged against `now`. */
export function envRows(envs: readonly runnerApi.EnvironmentView[], now: number): readonly EnvRow[] {
  return envs.map((env) => ({
    environmentId: env.environment_id,
    isHeld: env.chunk_id != null,
    chunkRef: env.chunk_id == null ? '' : compactRef(env.chunk_id),
    heldFor: heldForLabel(env.held_since, now),
  }));
}
