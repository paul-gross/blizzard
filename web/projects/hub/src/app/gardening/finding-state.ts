import type { hubApi, Tone } from 'fleet';

/**
 * The findings triage surface's own state classification
 * (`plans/garden/user-interface.md`'s "Triaging what's left" section) — shared here
 * so {@link FleetFindingList} and any later triage affordance (`reopen` on an
 * exited row) read the same three buckets off the wire rather than each
 * re-deriving them.
 *
 * `FindingView.live` is **not** this classification — it is a wire boolean that is
 * true only while `state` is `"live"`, so a `gone`-flagged finding reads
 * `live: false` on the wire even though it has not exited (a `gone` row stays open,
 * tinted, and actionable until a person confirms it). Whether a finding has exited
 * is the wire's own `FindingView.exit` (`outflow`, `withdrawn`, or `null` while
 * still open); every helper below classifies off `exit` and `state` for exactly
 * that reason.
 */

/** Whether a finding has exited — the wire carries a non-null `exit` (`outflow` or
 * `withdrawn`). An exited finding renders dimmed but present, never removed from
 * the list. */
export function isFindingExited(exit: hubApi.FindingExit | null | undefined): boolean {
  return exit != null;
}

/** Whether `state` is `'gone'` — still open (not counted as exited anywhere),
 * but flagged for review and rendered tinted. */
export function isFindingGoneFlagged(state: hubApi.FindingState): boolean {
  return state === 'gone';
}

/** A finding state → badge tone, on the shared `Tone` ladder, so every surface that
 * shows the state shows it the same color; pinned by `finding-state.spec.ts`'s "tones
 * each state by open, delivered, outflow, or withdrawn". */
const STATE_TONE: Readonly<Record<hubApi.FindingState, Tone>> = {
  live: 'running',
  gone: 'waiting',
  delivered: 'takeover',
  resolved: 'done',
  'gone-confirmed': 'done',
  'wont-fix': 'idle',
  'not-a-finding': 'idle',
  superseded: 'idle',
};

/** {@link STATE_TONE}'s lookup, falling back to `idle` for a state this build does
 * not know; pinned by `finding-state.spec.ts`'s "falls back to idle for a state this
 * build does not know". */
export function findingStateTone(state: string): Tone {
  const tones: Readonly<Partial<Record<string, Tone>>> = STATE_TONE;
  return tones[state] ?? 'idle';
}

/** A review-sourced finding's own severity → badge tone.
 * `blocking` reads `needs`, the same red a human-blocked chunk reads — a blocking
 * finding is exactly that, something standing in the way until a person addresses
 * it. `should-fix` reads `waiting`, the same amber-hi a parked chunk reads: real,
 * but not fatal to the round. */
const SEVERITY_TONE: Readonly<Record<hubApi.FindingSeverity, Tone>> = {
  blocking: 'needs',
  'should-fix': 'waiting',
};

/** {@link SEVERITY_TONE}'s lookup, falling back to `idle` for a severity this build
 * does not know — {@link findingStateTone}'s own reasoning. */
export function findingSeverityTone(severity: string): Tone {
  const tones: Readonly<Partial<Record<string, Tone>>> = SEVERITY_TONE;
  return tones[severity] ?? 'idle';
}
