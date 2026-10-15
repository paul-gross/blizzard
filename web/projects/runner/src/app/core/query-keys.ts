import { chunkDetailKey } from 'fleet';

/**
 * The TanStack Query keys the panel reads under, in one place — mirrors `fleet`'s
 * `hub`-namespaced `query-keys.ts` (the fleet/local split: local pages own their own
 * keys). A runner's own reads lead with its runner id, so two runners on one host never
 * share a cache entry and a frame naming one runner stales only that runner's keys. The
 * host's reads lead with `host`.
 */

/** Every host read's prefix — `GET /api/host/*`. Every live frame stales it. */
export const hostKey = ['host'] as const;
/** `GET /api/host/runners` — every runner the host serves or holds halted. */
export const hostRunnersKey = [...hostKey, 'runners'] as const;
/** `GET /api/host/workspaces` — the workspaces the host declares. */
export const hostWorkspacesKey = [...hostKey, 'workspaces'] as const;
/** `GET /api/host/environments` — the host's environments grouped by workspace. */
export const hostEnvironmentsKey = [...hostKey, 'environments'] as const;
/** `GET /api/host/subscriptions` — the host's subscriptions and who spends them. */
export const hostSubscriptionsKey = [...hostKey, 'subscriptions'] as const;
/** `GET /api/host/registration` — the host's registration at its hub. */
export const hostRegistrationKey = [...hostKey, 'registration'] as const;

/** `GET /api/auth/session` — whether the surface is gated, and the signed-in hub
 * username, behind the panel's username/logout control. The host's session, kept out of
 * {@link hostKey} so a live frame never re-reads it. */
export const sessionKey = ['auth', 'session'] as const;

/** One runner's leases — `GET /runners/{runner_id}/api/leases`. */
export function runnerLeasesKey(runnerId: string | null): readonly unknown[] {
  return [runnerId, 'leases'];
}

/**
 * One runner's dashboard — `GET /runners/{runner_id}/api/dashboard`: the panel's status
 * reads composed into one response: `runner` (identity, capacities, hub connectivity,
 * last tick), `asks`, `escalations`, `takeovers`, `facts`, `harness_health`, and
 * `fleet_summary`. One key for every rail this panel polls, so TanStack dedupes the N
 * components that inject it into one request.
 */
export function runnerDashboardKey(runnerId: string | null): readonly unknown[] {
  return [runnerId, 'dashboard'];
}

/**
 * One chunk's pass-through work items (issue title + labels) on one runner. Deliberately
 * its own key — never invalidated or refetched by the leases poll (the severable title
 * enrichment) — so it never collides with the chunk page's `work-items-detail` read.
 */
export function runnerChunkWorkItemsKey(runnerId: string | null, chunkId: string): readonly unknown[] {
  return [runnerId, 'chunk', chunkId, 'work-items'];
}

/**
 * One chunk's full detail aggregate on one runner — the chunk-detail dock's header,
 * pass-through-forwarded to the hub (`ChunkDetail.pause` is the only way this panel learns
 * a chunk is paused). Defined once by `fleet`'s {@link chunkDetailKey}, since the shared
 * chunk page and this dock read the same entry.
 */
export function runnerChunkDetailKey(runnerId: string, chunkId: string): readonly unknown[] {
  return chunkDetailKey(runnerId, chunkId);
}
