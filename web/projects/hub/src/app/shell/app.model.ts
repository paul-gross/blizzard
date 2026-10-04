import { type SseStatus } from 'fleet';

/** The top-level session gate: `loading` while the identity read is pending, `unauthenticated` without an identity,
 * `lobby` for an identity holding no permission, else `ready`. */
export function authState(
  isPending: boolean,
  me: { readonly permissions: readonly unknown[] } | null,
): 'loading' | 'unauthenticated' | 'lobby' | 'ready' {
  if (isPending) return 'loading';
  if (me === null) return 'unauthenticated';
  return me.permissions.length === 0 ? 'lobby' : 'ready';
}

/** The header's connection label: the live stream's reconnect first, then the health read's pending, failed, and
 * reported states — `ok` when the health read names no status. */
export function connectionLabel(
  streamState: SseStatus,
  healthPending: boolean,
  healthError: boolean,
  healthStatus: string | undefined,
): string {
  if (streamState === 'reconnecting') return 'reconnecting…';
  if (healthPending) return 'connecting…';
  if (healthError) return 'offline';
  return healthStatus ?? 'ok';
}
