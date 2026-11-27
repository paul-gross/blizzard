/*
 * Eager-shell entry point of the `fleet` shared library (`fleet/shell`):
 * named re-exports of exactly what an app's eager shell statically reaches
 * (`bzh:frontend-eager-shell-entry`, gated by `web:bundle-composition`).
 */

export { AppShell } from './lib/shell/app-shell/app-shell';
export { BoardHeader } from './lib/shell/board-header/board-header';
export { EVENT_SOURCE_FACTORY, FetchEventSource, SseService, type EventSourceFactory, type SseStatus } from './lib/sse/sse.service';
export { INVALIDATION_COALESCE_WINDOW_MS, LiveInvalidationSpine } from './lib/sse/live-invalidation-spine';
export { HUB_EVENT_TYPES, hubEventStreamUrl } from './lib/sse/hub-events';
export { HubEventKind, hubEventTypeName, type HubEventType, type HubEventData, type ActivityFact } from './lib/sse/hub-events.domain';
export { translateHubEvent } from './lib/sse/hub-events.translate';
export { openLookup } from './lib/core/open-lookup';
export { chunkDetailKey, chunkWorkItemsKey } from './lib/core/query-keys';
export { FLEET_CLOCK, injectNowSignal } from './lib/core/now-signal/now-signal';
export { RUNNER_HOST_NAMES, RUNNER_NAMES } from './lib/core/runner-display-name';
export { FlagStore, provideFlags, type FlagsRead } from './lib/core/flags/flag-store';
export { flagGuard } from './lib/core/flags/flag-guard';
export { APP_SCOPE, AppLink, AppScope, type AppScopePolicy, type InAppLink } from './lib/core/app-scope';

export { ViewportService } from './lib/core/viewport/viewport-service';
export { matchesMobileViewport } from './lib/core/viewport/matches-mobile-viewport';
export { provideViewportRenavigation } from './lib/core/viewport/viewport-renavigation';

export { KitTab, KitTabStrip } from './lib/kit/kit-tab';
export { KitChip } from './lib/kit/kit-chips';
export { KitSwatch } from './lib/kit/kit-swatch';
export { MobileTabBar, type MobileTabItem } from './lib/shell/mobile-chrome/mobile-tab-bar';

export { KitButton } from './lib/kit/kit-button';
export { KitErrorLine } from './lib/kit/kit-error-line';
export { errorMessage, problemType } from './lib/core/error-message';
export { LIVE_COVERED_POLL_BACKSTOP_MS } from './lib/core/polling/polling';
export { client as hubClient } from './lib/api/hub/client.gen';
export {
  chunkCountsApiChunkCountsGet,
  fleetSpendApiSpendGet,
  flagsApiFlagsGet,
  getProjectApiProjectsProjectGet,
  healthApiHealthGet,
  listHostsApiHostsGet,
  listChunksApiChunksGet,
  listDecisionsApiDecisionsGet,
  listOpenQuestionsApiQuestionsGet,
  listProjectsApiProjectsGet,
  listProvidersApiAuthProvidersGet,
  listRunnersApiRunnersGet,
  logoutApiAuthLogoutPost,
  meApiMeGet,
} from './lib/api/hub/sdk.gen';
export type {
  ChunkCountsView,
  ChunkSummary,
  ChunksPageView,
  DecisionView,
  FlagsResponse,
  FleetSpendView,
  HostRegistryView,
  MeResponse,
  MeTenant,
  ProjectNotFoundProblem,
  ProjectView,
  ProviderSummary,
  QuestionView,
  RunnerRegistryView,
} from './lib/api/hub/types.gen';
export { KitChipGroup } from './lib/kit/kit-chip-group';
export { KitChipOptionDirective } from './lib/kit/kit-chip-option';

export { laneCountInput, laneStatus } from './lib/core/chunk-lanes.translate';
export type { LaneCounts, LaneStatus } from './lib/core/chunk-lanes';

export type { HostFact, HostUsage, SubscriptionUsage } from './lib/core/host.domain';
export { hostConnection, sampleMiss } from './lib/core/host.translate';
export type { SubscriptionUsageView, HostSubscriptionUsageView } from './lib/api/hub';
