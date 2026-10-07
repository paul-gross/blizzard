/*
 * The loose formatting/display modules grouped into one sub-barrel —
 * `compactRef`, cost/token formatting, board lane/tone folds, and time formatting.
 * The implementation files stay at `lib/` root (no consumer import-path churn); this
 * barrel is purely the public re-export surface, one line per module.
 */

export { compactRef } from '../compact-ref';
export { RUNNER_NAME_SEPARATOR, runnerDisplayName, runnerTitle } from '../runner-display-name';
export { formatCost, formatTokens, hasCostFigure } from '../cost-format';
export { errorMessage } from '../error-message';
export { harnessName } from '../harness-name';
export { nodeStepKey, parseNodeStepKey } from '../node-step';
export { LANES, STATUS_LANE, STATUS_TONE, laneCounts, type Lane } from '../chunk-lanes';
export {
  formatWhen,
  formatClockTime,
  formatRefreshedAgo,
  formatAbsolute,
  formatAge,
  formatHeldFor,
  formatSeenAgo,
  ageMs,
  formatUtcYmd,
  formatLocalClockWithDay,
  type LocalClockWithDay,
} from '../when';
