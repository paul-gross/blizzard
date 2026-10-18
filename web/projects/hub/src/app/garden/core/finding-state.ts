import type { hubApi, Tone } from 'fleet';

/** The findings state classification — `exit` and `state` decide it; the wire's `FindingView.live` does not. */

/** Whether a finding has exited — the wire carries a non-null `exit` (`outflow` or `withdrawn`). */
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

/** A review-sourced finding's severity → badge tone. */
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
