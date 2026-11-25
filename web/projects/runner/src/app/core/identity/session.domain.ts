/** The identity the runner reports a request resolving to: whether its hub gates the panel, the
 * signed-in hub username, `null` when no session rode along, and whether that identity holds
 * superuser authority. */
export interface RunnerSession {
  readonly auth_enabled: boolean;
  readonly username: string | null;
  readonly superuser: boolean;
}

/**
 * The signed-in hub username a session carries, or `null` when there is none
 * to show — a `none`-mode hub (authless surface), an unresolved read, or oauth
 * with no session riding along.
 *
 * One owner for the fold because two components gate on it: `LocalIdentity`
 * renders itself off it, and the mobile shell gates its own `Log out` **menu
 * item** off it. The shell cannot reuse the identity block's copy — that
 * component is constructed inside the menu overlay, so its signal is still
 * unresolved on the overlay's first change detection, and a menu item that
 * appears a tick late is one `CdkMenu` has already skipped when setting initial
 * focus. The shell reads the same query itself, long resolved by the time a menu
 * opens; this keeps the two answers from drifting.
 */
export function signedInUsername(session: RunnerSession | undefined): string | null {
  return session?.auth_enabled ? (session.username ?? null) : null;
}

/** Whether the session resolves a signed-in hub user — the condition that ends a recovery cycle. */
export function resolvesUser(session: RunnerSession): boolean {
  return session.auth_enabled && !!session.username;
}

/** Whether the surface is gated and no session rode along — the `401` the recovery seam can fix. */
export function lacksSession(session: RunnerSession): boolean {
  return session.auth_enabled && !session.username;
}
