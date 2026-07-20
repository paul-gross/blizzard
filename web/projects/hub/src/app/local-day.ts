/** Local midnight, as the ISO-8601 instant `GET /api/spend?since=` expects
 * (issue #60) — "spend today" is the operator's own calendar day, not UTC's.
 * Shared by the desktop titlebar ({@link App}) and the mobile glance board
 * ({@link GlanceBoard}), so both read the same local-midnight window and share
 * one `injectHubFleetSpendQuery` cache entry rather than opening two. */
export function startOfLocalDayIso(): string {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
}
