/**
 * The fleet-wide derived-status color vocabulary (issue #78) — the union
 * already implicit in `local-panel`'s `MachineChunkStatus.tone` and the hub
 * board's lane coloring. {@link KitBadge} owns the tone→color ladder; a future
 * status-to-tone mapping (issue #81) populates onto this same union rather
 * than inventing a second one.
 *
 * Meaning, fixed by the hub board's existing scheme: `running` is live work
 * (amber), `needs` is human-blocked (red), `waiting`/`takeover` are
 * human-parked (amber-hi), `spawning` is starting up (cyan), `done` is landed
 * (green), `stale` reads as an alarm (red), `idle` is a spent/inert row (dim).
 */
export type Tone = 'running' | 'needs' | 'waiting' | 'takeover' | 'spawning' | 'stale' | 'done' | 'idle';
