import type { Tone } from 'fleet';

/**
 * The findings triage surface's own state classification
 * (`plans/garden/user-interface.md`'s "Triaging what's left" section) — shared here
 * so {@link FleetFindingList} and any later triage affordance (`reopen` on an
 * exited row) read the same three buckets off `FindingView.state` rather than each
 * re-deriving them.
 *
 * `FindingView.live` is **not** this classification — it is a wire boolean set by
 * `derive_liveness` (`src/blizzard/hub/domain/findings.py:102-126`) as
 * `live = (state == "live")`, so a `gone`-flagged finding reads `live: false` on the
 * wire even though it has not exited (a `gone` row stays open, tinted, and
 * actionable until a person confirms it). Every helper below classifies off `state`
 * directly for exactly that reason.
 */

/** The states the ground itself changed under — `EXIT_KINDS`'s outflow half
 * (`src/blizzard/hub/domain/findings.py:25-29`). */
export const FINDING_OUTFLOW_STATES: readonly string[] = ['resolved', 'gone-confirmed'];

/** The states a human judgment call withdrew — `EXIT_KINDS`'s withdrawn half,
 * same lines: the ground didn't move, a person decided the finding doesn't merit
 * standing regardless. */
export const FINDING_WITHDRAWN_STATES: readonly string[] = ['wont-fix', 'not-a-finding', 'superseded'];

/** Every state `EXIT_KINDS` names (`src/blizzard/hub/domain/findings.py:23`) — the
 * outflow and withdrawn sets combined. A finding in one of these states has exited:
 * it renders dimmed but present, never removed from the list. */
export const FINDING_EXIT_STATES: readonly string[] = [...FINDING_OUTFLOW_STATES, ...FINDING_WITHDRAWN_STATES];

/** `FindingView.state`'s own closed set — `"live"`, `"gone"`, `"delivered"`, or one of
 * `EXIT_KINDS` (`src/blizzard/hub/domain/findings.py`'s own doc comment on the field).
 * `'live'`, `'gone'`, and `'delivered'` (all still open — `delivered`
 * joins no `EXIT_KINDS` set) plus every exit state, derived from the same constants
 * above rather than a second hand-typed list, so this file stays the one place the
 * full vocabulary is spelled out. */
export const FINDING_STATES: readonly string[] = ['live', 'gone', 'delivered', ...FINDING_EXIT_STATES];

/** Whether `state` is one of {@link FINDING_EXIT_STATES}. */
export function isFindingExited(state: string): boolean {
  return FINDING_EXIT_STATES.includes(state);
}

/** Whether `state` is `'gone'` — still open (not counted as exited anywhere),
 * but flagged for review and rendered tinted. */
export function isFindingGoneFlagged(state: string): boolean {
  return state === 'gone';
}

/** A finding state → badge tone, on the shared `Tone` ladder, so every surface that
 * shows the state shows it the same color; pinned by `finding-state.spec.ts`'s "tones
 * each state by open, delivered, outflow, or withdrawn". */
const STATE_TONE: Readonly<Record<string, Tone>> = {
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
  return STATE_TONE[state] ?? 'idle';
}

/** A review-sourced finding's own severity → badge tone.
 * `blocking` reads `needs`, the same red a human-blocked chunk reads — a blocking
 * finding is exactly that, something standing in the way until a person addresses
 * it. `should-fix` reads `waiting`, the same amber-hi a parked chunk reads: real,
 * but not fatal to the round. */
const SEVERITY_TONE: Readonly<Record<string, Tone>> = {
  blocking: 'needs',
  'should-fix': 'waiting',
};

/** {@link SEVERITY_TONE}'s lookup, falling back to `idle` for a severity this build
 * does not know — {@link findingStateTone}'s own reasoning. */
export function findingSeverityTone(severity: string): Tone {
  return SEVERITY_TONE[severity] ?? 'idle';
}
