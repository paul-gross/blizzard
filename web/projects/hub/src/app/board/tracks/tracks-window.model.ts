const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
/** The hub's widest read, and how far back the present column looks for an environment's last step. */
export const LOOKBACK_MS = 14 * 24 * HOUR_MS;

/** The preset spans the window chips offer, newest-anchored. */
export const TRACK_SPANS = [
  { value: '6h', label: '6h', ms: 6 * HOUR_MS },
  { value: '24h', label: '24h', ms: 24 * HOUR_MS },
  { value: '3d', label: '3d', ms: 72 * HOUR_MS },
  { value: '7d', label: '7d', ms: 168 * HOUR_MS },
] as const;

/** The window the tracks show: `spanMs` wide, ending at `endMs`, or following now while `endMs` is `null`. */
export interface TrackWindow {
  readonly spanMs: number;
  readonly endMs: number | null;
}

export const DEFAULT_TRACK_WINDOW: TrackWindow = {
  spanMs: 24 * HOUR_MS,
  endMs: null,
};

/** A window resolved against a clock: its bounds in ms, and the `since`/`until` the read sends. */
export interface ResolvedTrackWindow {
  readonly startMs: number;
  readonly endMs: number;
  readonly since: string;
  /** Absent while following now — the read's open tail. */
  readonly until: string | undefined;
  readonly live: boolean;
}

/**
 * Resolve `window` against `nowMs`. A live window's end is floored to the minute, so it moves once a minute, not every
 * tick; a pinned one keeps the exact end the user left, so the tracks never snap away from where a pan or zoom landed.
 */
export function resolveTrackWindow(window: TrackWindow, nowMs: number): ResolvedTrackWindow {
  const live = window.endMs === null;
  const endMs = window.endMs ?? Math.floor(nowMs / MINUTE_MS) * MINUTE_MS;
  const startMs = endMs - window.spanMs;
  return {
    startMs,
    endMs,
    since: new Date(startMs).toISOString(),
    until: live ? undefined : new Date(endMs).toISOString(),
    live,
  };
}

/** The window a user's pan or zoom left on screen; one ending within a minute of now keeps following now. */
export function windowFromRange(startMs: number, endMs: number, nowMs: number): TrackWindow {
  const spanMs = Math.max(MINUTE_MS, endMs - startMs);
  return { spanMs, endMs: endMs >= nowMs - MINUTE_MS ? null : endMs };
}

/** The preset chip a window matches, or `null` once a pan or zoom has left the presets. */
export function spanChoice(window: TrackWindow): string | null {
  if (window.endMs !== null) return null;
  return TRACK_SPANS.find((span) => span.ms === window.spanMs)?.value ?? null;
}

/** The window a preset chip selects: that span, following now. */
export function windowForSpan(value: string, current: TrackWindow): TrackWindow {
  const span = TRACK_SPANS.find((s) => s.value === value);
  return span ? { spanMs: span.ms, endMs: null } : current;
}

/** A pinned window's read bounds. */
export interface TimelineRange {
  readonly since: string;
  readonly until: string;
}

/** The grid cells a pinned window's read snaps to — the smallest at least half its span. */
const RANGE_GRID_MS = [1, 3, 6, 12, 24, 48, 96].map((h) => h * HOUR_MS);

/**
 * The read bounds for a pinned window `[startMs, endMs)`: snapped outward to a grid a fraction of its span wide, so a
 * pan within a grid cell reuses the cached read, and kept within the hub's widest read.
 */
export function rangeFor(startMs: number, endMs: number): TimelineRange {
  const grid = RANGE_GRID_MS.find((g) => g >= (endMs - startMs) / 2) ?? RANGE_GRID_MS[RANGE_GRID_MS.length - 1];
  let since = Math.floor(startMs / grid) * grid;
  const until = Math.ceil(endMs / grid) * grid;
  if (until - since > LOOKBACK_MS) since = until - LOOKBACK_MS;
  return { since: new Date(since).toISOString(), until: new Date(until).toISOString() };
}

/** The live cover a span needs: the smallest preset-sized cover at least that wide, up to the hub's widest read. */
export function liveCoverFor(spanMs: number): number {
  return [6, 24, 72, 168, 336].map((h) => h * HOUR_MS).find((c) => c >= spanMs) ?? LOOKBACK_MS;
}
