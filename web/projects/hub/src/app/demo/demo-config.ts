/**
 * Demo mode's configuration, as the URL's query string carries it.
 *
 * Demo mode is the unadvertised kiosk pilot: `?demo=true` on any hub URL hands
 * the board to {@link DemoDirector}, which tours the fleet unattended on a wall
 * screen. Nothing in the UI announces it and nothing links to it — the query
 * string is the whole switch.
 *
 * The config is **latched once, at app construction**, from the browser's own
 * `location.search` rather than from the router. Two reasons: the latch happens
 * before the router's first navigation can rewrite anything, and demo state is
 * deliberately in-memory from then on, so a navigation that drops the params
 * cannot switch the demo off mid-tour. {@link demoQueryParams} re-emits the raw
 * values the latch saw onto every navigation the director makes, which is what
 * makes a kiosk reload (see `demo-kiosk.ts`) resume the demo instead of landing
 * on a plain board.
 *
 * Durations accept a bare number of seconds or an `s`/`m`/`h` suffix, so
 * `demo_swap_chunk_interval=900`, `=15m`, and `=0.25h` all mean the same thing.
 */
export interface DemoConfig {
  /** Whether `?demo` asked for demo mode at all. */
  readonly enabled: boolean;

  /** How long one chunk holds the screen before the director swaps to another
   * — the whole cycle, board dwell included (`demo_swap_chunk_interval`). */
  readonly swapChunkMs: number;

  /** How long each artifact holds the screen within a cycle, and therefore how
   * long its scroll takes (`demo_artifact_interval`). */
  readonly artifactMs: number;

  /** How long the board dock's panes take to scroll to their bottom before the
   * director descends into the chunk page (`demo_board_scroll`). Clamped to
   * {@link MAX_BOARD_SHARE} of {@link swapChunkMs} — see there for why. */
  readonly boardScrollMs: number;

  /** Reload the page at a swap boundary once it has been up this long, `0` to
   * never (`demo_reload_after`). A backstop under the deploy-stamp check — see
   * `demo-kiosk.ts`. */
  readonly maxUptimeMs: number;

  /** The demo params exactly as they arrived, re-emitted on every demo
   * navigation so the URL on screen is always one a reload can resume from. */
  readonly raw: Readonly<Record<string, string>>;
}

/** Every query param demo mode reads. Anything else in the URL is not its business. */
const DEMO_PARAMS = [
  'demo',
  'demo_swap_chunk_interval',
  'demo_artifact_interval',
  'demo_board_scroll',
  'demo_reload_after',
] as const;

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;

const DEFAULTS = {
  swapChunkMs: 2 * MINUTE,
  artifactMs: 20 * SECOND,
  boardScrollMs: 60 * SECOND,
  maxUptimeMs: 1 * HOUR,
} as const;

const UNITS: Readonly<Record<string, number>> = { s: SECOND, m: MINUTE, h: HOUR };

/**
 * The most of one cycle the board dwell may claim.
 *
 * Each dial is sane alone but the pair is not: the cycle deadline is struck
 * before the board dwell, so a `demo_board_scroll` at or past
 * `demo_swap_chunk_interval` leaves the artifact tour no time at all — the
 * director descends into the chunk page and swaps away in the same breath,
 * which is precisely the flicker the board dwell exists to prevent. Clamping
 * here rather than at the point of use keeps the whole "what did these params
 * actually resolve to" question answerable from {@link DemoConfig} alone.
 *
 * Half is the loosest value that still guarantees the tour a real share of the
 * cycle. The shipped defaults (60s board, 2m cycle) sit exactly at it, so the
 * clamp is a ceiling on misconfiguration, not a tax on the normal case.
 */
const MAX_BOARD_SHARE = 0.5;

/**
 * Read a duration param: a bare number is seconds, an `s`/`m`/`h` suffix names
 * its own unit. Anything unparseable — or negative — falls back rather than
 * putting the kiosk into a zero-length or backwards cycle it can never leave.
 */
export function parseDuration(raw: string | null, fallbackMs: number): number {
  if (raw === null) return fallbackMs;
  const match = /^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$/i.exec(raw);
  if (match === null) return fallbackMs;
  const value = Number(match[1]) * (UNITS[match[2].toLowerCase()] ?? SECOND);
  return Number.isFinite(value) && value >= 0 ? value : fallbackMs;
}

/** Whether a `?demo` value asks demo mode on. Bare `?demo` (empty value) counts. */
function readsAsOn(raw: string | null): boolean {
  if (raw === null) return false;
  return raw === '' || ['true', '1', 'yes', 'on'].includes(raw.toLowerCase());
}

/** Parse {@link DemoConfig} out of a query string (`location.search`). */
export function readDemoConfig(search: string): DemoConfig {
  const params = new URLSearchParams(search);
  const raw: Record<string, string> = {};
  for (const name of DEMO_PARAMS) {
    const value = params.get(name);
    if (value !== null) raw[name] = value;
  }

  const swapChunkMs = parseDuration(params.get('demo_swap_chunk_interval'), DEFAULTS.swapChunkMs);

  return {
    enabled: readsAsOn(params.get('demo')),
    swapChunkMs,
    artifactMs: parseDuration(params.get('demo_artifact_interval'), DEFAULTS.artifactMs),
    boardScrollMs: Math.min(
      parseDuration(params.get('demo_board_scroll'), DEFAULTS.boardScrollMs),
      swapChunkMs * MAX_BOARD_SHARE,
    ),
    maxUptimeMs: parseDuration(params.get('demo_reload_after'), DEFAULTS.maxUptimeMs),
    raw,
  };
}

/**
 * The demo params to merge into a router navigation. `demo` is forced on even
 * when the latch came from a bare `?demo`, so the URL a kiosk reloads is always
 * explicit.
 */
export function demoQueryParams(config: DemoConfig): Record<string, string> {
  return { ...config.raw, demo: 'true' };
}
