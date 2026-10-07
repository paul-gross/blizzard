import type { Tone } from 'fleet';

/**
 * A hub record's enabled/retired lifecycle → badge {@link Tone} — the one owner
 * every lifecycle label reads (garden routines and scopes, graph versions), so
 * no surface re-types its own colour for it (`bzh:frontend-formatters`).
 *
 * Chosen for colour, not for `Tone`'s documented meanings: `enabled` reuses
 * `spawning`'s cyan (`Tone`'s only cyan), and `retired` reuses `stale`'s red,
 * reading as the alarm a deliberately disabled record should.
 */
export function lifecycleTone(retired: boolean): Tone {
  return retired ? 'stale' : 'spawning';
}
