/*
 * The loose formatting/display modules grouped into one sub-barrel —
 * `compactRef`, `shortCommitHash`, cost/token formatting, the open-vocabulary lookup, board lane/tone folds, and time formatting.
 * The implementation files stay at `lib/` root (no consumer import-path churn); this
 * barrel is purely the public re-export surface, one line per module.
 */

export { compactRef } from '../compact-ref';
export { shortCommitHash } from '../commit-hash';
export { RUNNER_HOST_NAMES, injectRunnerHostNames, runnerDisplayName, runnerTitle } from '../runner-display-name';
export { formatCost, formatTokens, hasCostFigure } from '../cost-format';
export { errorMessage } from '../error-message';
export { harnessName } from '../harness-name';
export { nodeStepKey, parseNodeStepKey } from '../node-step';
export { openLookup, unlisted } from '../open-lookup';
export { DONE_LANE, LANES, STATUS_LANE, STATUS_TONE, UNKNOWN_LANE, chunkLane, laneCounts, unknownLane, type Lane } from '../chunk-lanes';
export {
  formatWhen,
  formatClockTime,
  formatRefreshedAgo,
  formatAbsolute,
  formatAge,
  formatAgo,
  formatHeldFor,
  formatSeenAgo,
  formatSeenTag,
  ageMs,
  formatUtcYmd,
  formatLocalClockWithDay,
  type LocalClockWithDay,
} from '../when';
