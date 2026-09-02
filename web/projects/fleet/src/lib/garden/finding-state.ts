/**
 * The findings triage surface's own state classification (blizzard#401 Phase 4,
 * `plans/garden/user-interface.md`'s "Triaging what's left" section) — shared here
 * so {@link FleetFindingList} and any later triage affordance (Phase 3's `reopen`
 * on an exited row) read the same three buckets off `FindingView.state` rather than
 * each re-deriving them.
 *
 * `FindingView.live` is **not** this classification — it is a wire boolean set by
 * `derive_liveness` (`src/blizzard/hub/domain/findings.py:102-126`) as
 * `live = (state == "live")`, so a `gone`-flagged finding reads `live: false` on the
 * wire even though it has not exited (D8: a `gone` row stays open, tinted, and
 * actionable until a person confirms it). Every helper below classifies off `state`
 * directly for exactly that reason.
 */

/** The states the ground itself changed under (D2) — `EXIT_KINDS`'s outflow half
 * (`src/blizzard/hub/domain/findings.py:25-29`). */
export const FINDING_OUTFLOW_STATES: readonly string[] = ['resolved', 'gone-confirmed'];

/** The states a human judgment call withdrew (D2) — `EXIT_KINDS`'s withdrawn half,
 * same lines: the ground didn't move, a person decided the finding doesn't merit
 * standing regardless. */
export const FINDING_WITHDRAWN_STATES: readonly string[] = ['wont-fix', 'not-a-finding', 'superseded'];

/** Every state `EXIT_KINDS` names (`src/blizzard/hub/domain/findings.py:22`) — the
 * outflow and withdrawn sets combined. A finding in one of these states has exited:
 * it renders dimmed but present, never removed from the list. */
export const FINDING_EXIT_STATES: readonly string[] = [...FINDING_OUTFLOW_STATES, ...FINDING_WITHDRAWN_STATES];

/** Whether `state` is one of {@link FINDING_EXIT_STATES}. */
export function isFindingExited(state: string): boolean {
  return FINDING_EXIT_STATES.includes(state);
}

/** Whether `state` is one of {@link FINDING_OUTFLOW_STATES}. */
export function isFindingOutflow(state: string): boolean {
  return FINDING_OUTFLOW_STATES.includes(state);
}

/** Whether `state` is one of {@link FINDING_WITHDRAWN_STATES}. */
export function isFindingWithdrawn(state: string): boolean {
  return FINDING_WITHDRAWN_STATES.includes(state);
}

/** Whether `state` is `'gone'` — still open (D8: not counted as exited anywhere),
 * but flagged for review and rendered tinted. */
export function isFindingGoneFlagged(state: string): boolean {
  return state === 'gone';
}
