import { ageMs } from 'fleet';

/** The DONE window `listChunksApiChunksGet` keeps under `board_window`: chunks finished in the last
 * `BOARD_DONE_WINDOW_HOURS`, plus the newest few however old. */
export const BOARD_DONE_WINDOW_HOURS = 48;

/** Whether `at` falls in the board's done window as of `now` — the one test the phone's recent-done groups share. */
export function withinDoneWindow(at: string | null | undefined, now: number): boolean {
  const age = ageMs(at, now);
  return age !== null && age <= BOARD_DONE_WINDOW_HOURS * 60 * 60 * 1000;
}
