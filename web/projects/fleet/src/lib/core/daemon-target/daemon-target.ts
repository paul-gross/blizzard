import type { Client } from '../../api/hub/client';

/**
 * The daemon a plane-generic read crosses to — the hub, or one runner a host serves.
 * `client` is the generated transport; a runner target also names the `runner_id` every
 * runner route is addressed under (`/runners/{runner_id}/api/…`), which is also the
 * leading segment of that runner's query keys ({@link targetKeyRoot}).
 */
export type DaemonTarget =
  | { readonly plane: 'hub'; readonly client: Client }
  | { readonly plane: 'runner'; readonly client: Client; readonly runnerId: string };

/** The leading segment of a target's query keys — `'hub'`, or the runner's id, so two
 * runners on one host never share a cache entry. */
export function targetKeyRoot(target: DaemonTarget): string {
  return target.plane === 'hub' ? 'hub' : target.runnerId;
}
