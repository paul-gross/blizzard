/**
 * Age rendering shared by the local panel's views — one owner for the
 * `-34s` / `-12m` / `-1h04m` heartbeat shorthand, the `42m` held-for
 * shorthand, and the browser-clock caveats both carry.
 *
 * Liveness is decided where both instants share one clock — the runner, via
 * the server-derived `state` every row already renders. Ages computed here are
 * decoration against the *browser's* clock, and a browser's clock must never
 * make a correctness call (`bzh:utc-instants`): a small negative delta (up to
 * {@link SKEW_TOLERANCE_MS}) is benign browser-vs-runner skew and floors at
 * zero; past that bound the caller renders `—` and lets the server-derived
 * state carry the meaning.
 */

/** Bounded tolerance for benign browser-vs-server clock skew (`bzh:utc-instants`). */
export const SKEW_TOLERANCE_MS = 60_000;

/** `-34s` / `-12m` / `-1h04m` — a heartbeat-style age; negatives floor at `-0s`. */
export function formatAge(deltaMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(deltaMs / 1000));
  if (totalSeconds < 60) return `-${totalSeconds}s`;
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) return `-${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `-${hours}h${String(minutes).padStart(2, '0')}m`;
}

/** `42s` / `42m` / `1h04m` / `3d` — an unsigned held-for duration; negatives floor at `0s`. */
export function formatHeldFor(deltaMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(deltaMs / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const totalHours = Math.floor(totalMinutes / 60);
  if (totalHours < 24) return `${totalHours}h${String(totalMinutes % 60).padStart(2, '0')}m`;
  return `${Math.floor(totalHours / 24)}d`;
}

/**
 * Parses an ISO instant and returns its age in ms against `now`, or `null`
 * when the value is absent, unparsable, or ahead of the browser clock by more
 * than {@link SKEW_TOLERANCE_MS} (the naive-timestamp failure `bzh:utc-instants`
 * exists to catch — render `—`, don't guess). A small negative floors at 0.
 */
export function ageMs(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return null;
  const delta = now - parsed;
  if (delta < -SKEW_TOLERANCE_MS) return null;
  return Math.max(0, delta);
}
