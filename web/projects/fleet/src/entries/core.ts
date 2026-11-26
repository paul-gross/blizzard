/* The `fleet/core` entry point: the light core concerns. */

export { APP_SCOPE, AppLink, AppPath, AppScope, type AppScopePolicy, type InAppLink } from '../lib/core/app-scope';
export { laneCountInput, laneStatus } from '../lib/core/chunk-lanes.translate';
export {
  chunkLane as domainChunkLane,
  laneCounts as domainLaneCounts,
  STATUS_LANE as domainStatusLane,
  STATUS_TONE as domainStatusTone,
  unknownLane as domainUnknownLane,
  DONE_LANE,
  type Lane,
  type LaneCounts,
  LANES,
  type LaneStatus,
  laneTone,
  UNKNOWN_LANE,
} from '../lib/core/chunk-lanes';
export { createCopyFlash } from '../lib/core/clipboard/copy-flash';
export { shortCommitHash } from '../lib/core/commit-hash';
export { compactRef } from '../lib/core/compact-ref';
export { formatCost, formatTokens, hasCostFigure } from '../lib/core/cost-format';
export { type DaemonTarget, targetKeyRoot } from '../lib/core/daemon-target/daemon-target';
export { BrandMark } from '../lib/core/design/brand-mark';
export { errorMessage, problemType } from '../lib/core/error-message';
export { flagGuard } from '../lib/core/flags/flag-guard';
export { type FlagsRead, FlagStore, provideFlags } from '../lib/core/flags/flag-store';
export { readRefusal } from '../lib/core/forms/refusal.translate';
export { readWholeNumber } from '../lib/core/forms/whole-number.domain';
export { harnessName } from '../lib/core/harness-name';
export type {
  HostConnection,
  HostFact,
  HostUsage,
  RunnerSubscription,
  SubscriptionUsage,
  UnknownHostValue,
} from '../lib/core/host.domain';
export { hostConnection, renewalFailure, renewalResult, sampleMiss } from '../lib/core/host.translate';
export { injectPendingMutationVariables, isPendingFor } from '../lib/core/mutation-pending/pending-state';
export { nodeStepKey, parseNodeStepKey } from '../lib/core/node-step';
export { FLEET_CLOCK, injectNowSignal } from '../lib/core/now-signal/now-signal';
export { openLookup, unlisted } from '../lib/core/open-lookup';
export { LIVE_COVERED_POLL_BACKSTOP_MS, RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from '../lib/core/polling/polling';
export {
  chunkDetailKey,
  chunkTranscriptSegmentKey,
  chunkTranscriptsKey,
  chunkWorkItemsKey,
  type TranscriptPlane,
} from '../lib/core/query-keys';
export { asyncState, asyncStateOf, type AsyncStateQuery, restingAsyncState } from '../lib/core/query-state/query-state';
export {
  injectRunnerHostNames,
  injectRunnerNames,
  RUNNER_HOST_NAMES,
  RUNNER_NAMES,
  runnerBoardLabel,
  runnerBoardParts,
  type RunnerBoardParts,
  runnerDisplayName,
  runnerTitle,
} from '../lib/core/runner-display-name';
export { SessionEfficiency } from '../lib/core/session-efficiency-view';
export type { SessionEfficiencyInput } from '../lib/core/session-efficiency';
export { parseSettingText, settingValueText } from '../lib/core/setting-value.domain';
export type { Urgency, UrgencyReference } from '../lib/core/urgency/urgency.domain';
export { injectChunkUrlSelection } from '../lib/core/url-selection/url-selection';
export { FleetWhen } from '../lib/core/when-display/fleet-when';
export {
  ageMs,
  formatAbsolute,
  formatAge,
  formatAgo,
  formatClockTime,
  formatHeldFor,
  formatLocalClockWithDay,
  formatRefreshedAgo,
  formatSeenAgo,
  formatSeenTag,
  formatUtcYmd,
  formatWhen,
  formatWhenParts,
  type LocalClockWithDay,
} from '../lib/core/when';
export { zoneOptions } from '../lib/core/zones/zones';
