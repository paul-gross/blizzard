/** The gardening routine panel's fixed reporting window (AC 3, AC 4) — the trend and
 * measurement reads share one window, `[since, until)`, cut to the last four weeks. A
 * pure function of `nowMs` so a spec drives it without a real clock. */
export interface RoutineWindow {
  readonly since: string;
  readonly until: string;
  readonly introducedBoundary: string;
  readonly periodDays: number;
  readonly label: string;
}

const WINDOW_DAYS = 28;
const PERIOD_DAYS = 7;
const DAY_MS = 24 * 60 * 60 * 1000;

export function defaultRoutineWindow(nowMs: number): RoutineWindow {
  const until = new Date(nowMs);
  const since = new Date(nowMs - WINDOW_DAYS * DAY_MS);
  return {
    since: since.toISOString(),
    until: until.toISOString(),
    introducedBoundary: since.toISOString(),
    periodDays: PERIOD_DAYS,
    label: `last ${WINDOW_DAYS} days`,
  };
}
