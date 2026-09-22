/*
 * The loose formatting/display modules grouped into one sub-barrel (issue #82) —
 * `compactRef`, cost/token formatting, board lane/tone folds, and time formatting.
 * The implementation files stay at `lib/` root (no consumer import-path churn); this
 * barrel is purely the public re-export surface, one line per module.
 */

export { compactRef, ENTITY_DISPLAY, type EntityDisplay } from '../compact-ref';
export { formatCost, formatCostEstimate, formatTokens } from '../cost-format';
export { errorMessage } from '../error-message';
export { nodeStepKey, parseNodeStepKey } from '../node-step';
export { LANES, STATUS_LANE, STATUS_TONE, laneFor, type Lane } from '../chunk-lanes';
export {
  formatWhen,
  formatAbsolute,
  formatAge,
  formatHeldFor,
  formatSeenAgo,
  ageMs,
  formatUtcYmd,
  formatLocalClockWithDay,
  type LocalClockWithDay,
  SKEW_TOLERANCE_MS,
} from '../when';
// formatClockTime (activity-panel.ts) is intentionally not re-exported here — it
// has exactly one fleet-internal caller today, which imports it directly from
// `../when`; no consumer outside this library needs it.
