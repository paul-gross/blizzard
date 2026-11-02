import type { Tone } from '../../kit/tone';

/**
 * A timeline verdict's {@link Tone}, keyed by the graph's choice name — a graph-authored name,
 * not a generated vocabulary, so the table holds the choices the timeline colors and every
 * other reads `running`. A verdict that looped the chunk back, collided or was refused reads
 * `needs`; the in-flight verb and a cross-graph hop read `spawning`; a parked verb or an
 * operator's restart reads `waiting`.
 */
const VERDICT_TONE: Readonly<Record<string, Tone>> = {
  fail: 'needs',
  conflict: 'needs',
  'needs-human': 'needs',
  bounced: 'needs',
  run: 'spawning',
  migrated: 'spawning',
  waiting: 'waiting',
  paused: 'waiting',
  restarted: 'waiting',
};

/** The {@link Tone} a verdict reads in — `running` for a choice the table does not hold, or none. */
export function verdictTone(choice: string | null): Tone {
  return choice !== null && Object.hasOwn(VERDICT_TONE, choice) ? VERDICT_TONE[choice] : 'running';
}
