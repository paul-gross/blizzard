import { type GraphLifecycle, openLookup, type Tone } from 'fleet';

/**
 * A hub record's enabled/retired lifecycle → badge {@link Tone} — the one owner
 * every lifecycle label reads (garden routines and scopes, graph versions, and the admin
 * secrets, forges, repositories and work sources), so
 * no surface re-types its own colour for it (`bzh:frontend-formatters`).
 *
 * Chosen for colour, not for `Tone`'s documented meanings: `enabled` reuses
 * `spawning`'s cyan (`Tone`'s only cyan), and `retired` reuses `stale`'s red,
 * reading as the alarm a deliberately disabled record should.
 */
export function lifecycleTone(retired: boolean): Tone {
  return retired ? 'stale' : 'spawning';
}

/**
 * The built-in marker's {@link Tone} — grey. Built-in is a provenance marker, not a lifecycle
 * state, so it never borrows {@link lifecycleTone}'s cyan "enabled" colour; the work-source
 * list and the project link panel both read it.
 */
export const builtInTone: Tone = 'idle';

/**
 * A graph version's wire `lifecycle` → badge {@link Tone}. `effective` and `retired` come from
 * {@link lifecycleTone}; `superseded`, and a lifecycle this build does not know, reuse `idle`'s dim.
 */
export const graphLifecycleTone = openLookup<GraphLifecycle, Tone>(
  { effective: lifecycleTone(false), superseded: 'idle', retired: lifecycleTone(true) },
  () => 'idle',
);
