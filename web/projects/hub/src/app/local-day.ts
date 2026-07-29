/** Local midnight, as the ISO-8601 instant `GET /api/spend?since=` expects
 * (issue #60) — "spend today" is the operator's own calendar day, not UTC's.
 * Shared by the desktop titlebar ({@link App}) and the mobile glance board
 * ({@link GlanceBoard}), so both read the same local-midnight window and share
 * one `injectHubFleetSpendQuery` cache entry rather than opening two. */
export function startOfLocalDayIso(): string {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
}

/** The local midnight *before* {@link startOfLocalDayIso}'s — "yesterday"'s own
 * start (issue #183). The two share one owner of the day boundary so yesterday
 * rolls over with today by construction: the header's yesterday window is
 * `[startOfPreviousLocalDayIso(), startOfLocalDayIso())`, and a caller reading
 * both a tick apart either side of midnight can never see them disagree about
 * where today starts. */
export function startOfPreviousLocalDayIso(): string {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1).toISOString();
}
