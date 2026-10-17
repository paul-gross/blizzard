/**
 * The fleet-wide derived-status color vocabulary. {@link KitBadge} owns the tone→color ladder.
 *
 * Meaning: `running` is live work, `needs` is human-blocked, `waiting`/`takeover`
 * are parked — human-parked when read off a status (`waiting_on_human`/`paused`), or
 * machine-parked when read off a chunk's dependency marking — `spawning` is starting
 * up, `done` is landed, `stale` reads as an alarm, `idle` is a spent/inert row.
 */
export type Tone = 'running' | 'needs' | 'waiting' | 'takeover' | 'spawning' | 'stale' | 'done' | 'idle';
