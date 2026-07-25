/**
 * The fleet-wide time-formatting layer (issue #81) — every absolute and
 * relative timestamp rendering resolves through here, so a "fix formatting"
 * change is one file instead of a scan across both libraries
 * (`bzh:frontend-formatters`).
 *
 * Liveness is always decided where both instants share one clock — the
 * server, via a server-derived state field the caller already renders. Every
 * function below is *decoration* against the *browser's* clock, and a
 * browser's clock must never make a correctness call (`bzh:utc-instants`,
 * `blizzard-harness:/standards/wire.md`): a bounded negative delta (up to
 * {@link SKEW_TOLERANCE_MS}) is benign browser-vs-server skew and floors at
 * zero; past that bound the caller renders `—` and lets the server-derived
 * state carry the meaning.
 */

/** Days between `d`'s local date and `now`'s local date, rounded so a small
 * intra-day skew never bumps the boundary — the "how many local midnights
 * back" shared by {@link formatWhen} and {@link formatLocalClockWithDay}, the
 * two callers that need it (`bzh:frontend-formatters`: one derivation, not a
 * copy per caller). */
function localDaysAgo(d: Date, now: Date): number {
  return Math.round(
    (new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() -
      new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) /
      86_400_000,
  );
}

/** Compact recency stamp for a board timestamp — the closer the instant, the shorter
 * the text: today reads as bare `HH:MM`, yesterday as `Yesterday HH:MM`, anything
 * older as the date alone (`2026/07/16` — a day-old judgement's minute no longer
 * matters). Local time, 24-hour clock, empty string for an unparseable input.
 *
 * `now` is injectable for tests only; callers pass the timestamp alone. */
export function formatWhen(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number): string => `${n}`.padStart(2, '0');
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const daysAgo = localDaysAgo(d, now);
  if (daysAgo <= 0) return hm;
  if (daysAgo === 1) return `Yesterday ${hm}`;
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
}

/** Local `HH:MM:SS` plus day context, as returned by {@link formatLocalClockWithDay} —
 * `day` is `null` when the instant's local date is today. */
export interface LocalClockWithDay {
  /** `Yesterday`, `yyyy-mm-dd`, or `null` when the instant's local date is today. */
  day: string | null;
  /** Zero-padded local `HH:MM:SS`. */
  time: string;
}

/**
 * Local `HH:MM:SS` plus day context for a log-style read — the runner fact log
 * and transcript's shared instant shape (issue #136, `bzh:frontend-formatters`).
 * The time is always present; when the instant's local date isn't today, `day`
 * carries `Yesterday` or the `yyyy-mm-dd` date for the caller to render on the
 * line above. Unlike {@link formatWhen} (which drops the time for older dates
 * — a board stamp where only the day still matters), a log entry's time is
 * always the primary read, so it's never dropped.
 *
 * `now` is injectable for tests only; callers pass the timestamp alone. Returns
 * `null` for an absent/unparseable input — the caller supplies its own
 * fallback text (e.g. `—`).
 */
export function formatLocalClockWithDay(iso: string | null | undefined, now: Date = new Date()): LocalClockWithDay | null {
  if (iso === null || iso === undefined) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const time = formatClockTime(d.getTime());
  const daysAgo = localDaysAgo(d, now);
  if (daysAgo <= 0) return { day: null, time };
  if (daysAgo === 1) return { day: 'Yesterday', time };
  const pad = (n: number): string => `${n}`.padStart(2, '0');
  return { day: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time };
}

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

/**
 * A compact "seen 12s ago" liveness label from a last-seen instant — the hub
 * runner registry's rendering (`runner-view.ts`). `online` is the
 * server-derived fallback this reads when the instant is absent, unparsable,
 * or beyond {@link SKEW_TOLERANCE_MS} in the future — never a confident `0s`
 * for a stamp that far off.
 */
export function formatSeenAgo(lastSeenAt: string, online: boolean, now: number = Date.now()): string {
  const delta = ageMs(lastSeenAt, now);
  if (delta === null) return online ? 'online' : 'offline';
  const secondsAgo = Math.round(delta / 1000);
  if (secondsAgo < 60) return `seen ${secondsAgo}s ago`;
  const minutesAgo = Math.round(secondsAgo / 60);
  if (minutesAgo < 60) return `seen ${minutesAgo}m ago`;
  return `seen ${Math.round(minutesAgo / 60)}h ago`;
}

/** `YYYYMMDD` from an ISO instant, rendered in UTC — empty string for an absent or
 * unparseable input. The chunk-detail graph label's creation-date suffix (issue #102,
 * `fact-graph`'s `#<name>-<YYYYMMDD>` form). */
export function formatUtcYmd(iso: string | null | undefined): string {
  if (iso === null || iso === undefined) return '';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '';
  return new Date(ms).toISOString().slice(0, 10).replace(/-/g, '');
}

/** Zero-padded local `HH:MM:SS` for an epoch-ms instant — the event log's
 * per-row arrival clock (`event-log-panel.ts`), and the time half of
 * {@link formatLocalClockWithDay} above. */
export function formatClockTime(atMs: number): string {
  const d = new Date(atMs);
  const pad = (n: number): string => `${n}`.padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
