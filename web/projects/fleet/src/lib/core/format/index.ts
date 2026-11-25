/*
 * The loose formatting/display modules grouped into one sub-barrel —
 * `compactRef`, `shortCommitHash`, cost/token formatting, the open-vocabulary lookup, board lane/tone folds, and time formatting.
 * The implementation files stay at `lib/` root (no consumer import-path churn); this
 * barrel is purely the public re-export surface, one line per module.
 */

export { compactRef } from '../compact-ref';
export { shortCommitHash } from '../commit-hash';
export { RUNNER_HOST_NAMES, RUNNER_NAMES, injectRunnerHostNames, injectRunnerNames, runnerBoardLabel, runnerBoardParts, type RunnerBoardParts, runnerDisplayName, runnerTitle } from '../runner-display-name';
export { formatCost, formatTokens, hasCostFigure } from '../cost-format';
export { type SessionEfficiencyInput } from '../session-efficiency';
export { SessionEfficiency } from '../session-efficiency-view';
export { errorMessage, problemType } from '../error-message';
export { harnessName } from '../harness-name';
export { nodeStepKey, parseNodeStepKey } from '../node-step';
export { openLookup, unlisted } from '../open-lookup';
export { parseSettingText, settingValueText } from '../setting-value.domain';
export { DONE_LANE, LANES, UNKNOWN_LANE, laneTone, chunkLane as domainChunkLane, unknownLane as domainUnknownLane, laneCounts as domainLaneCounts, STATUS_LANE as domainStatusLane, STATUS_TONE as domainStatusTone, type LaneStatus, type LaneCounts, type Lane } from '../chunk-lanes';
export {
  formatWhen,
  formatWhenParts,
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
