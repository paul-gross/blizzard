/*
 * Eager-shell entry point of the `fleet` shared library (`fleet/shell`):
 * named re-exports of exactly what an app's eager shell statically reaches
 * (`bzh:frontend-eager-shell-entry`, gated by `web:bundle-composition`).
 */

export { AppShell } from './lib/shell/app-shell/app-shell';
export { BoardHeader } from './lib/shell/board-header/board-header';
export { FleetLiveUpdates } from './lib/sse/fleet-live';
export { EVENT_SOURCE_FACTORY, FetchEventSource, type EventSourceFactory } from './lib/sse/sse.service';
export { FLEET_CLOCK, injectNowSignal } from './lib/core/now-signal';
export { HUB_PROJECT_FILTER, injectProjectFilter } from './lib/core/project-filter';
export { FlagStore, provideFlags, type FlagsRead, type FlagsView } from './lib/core/flags/flag-store';
export { flagGuard } from './lib/core/flags/flag-guard';
export { IN_APP_LINK, LensLink, type InAppLink } from './lib/core/lens-link';

export { ViewportService } from './lib/core/viewport/viewport-service';
export { matchesMobileViewport } from './lib/core/viewport/matches-mobile-viewport';
export { provideViewportRenavigation } from './lib/core/viewport/viewport-renavigation';

export { KitTab, KitTabStrip } from './lib/kit/kit-tab';
export { KitChip } from './lib/kit/kit-chips';
export { KitSwatch } from './lib/kit/kit-swatch';
export { MobileTabBar, type MobileTabItem } from './lib/shell/mobile-chrome/mobile-tab-bar';

export { KitButton } from './lib/kit/kit-button';
export { errorMessage } from './lib/core/error-message';
export { LIVE_COVERED_POLL_BACKSTOP_MS } from './lib/core/polling/polling';
export {
  hubAuthProvidersKey,
  hubBoardChunksKey,
  hubChunkCountsKey,
  hubDecisionsKey,
  hubFlagsKey,
  hubFleetSpendKey,
  hubHealthKey,
  hubMeKey,
  hubProjectKey,
  hubProjectLinksKey,
  hubProjectsKey,
  hubQuestionsKey,
  lensedKey,
  type ProjectKeyPart,
} from './lib/core/query-keys';
export { client as hubClient } from './lib/api/hub/client.gen';
export {
  chunkCountsApiChunkCountsGet,
  fleetSpendApiSpendGet,
  flagsApiFlagsGet,
  getProjectApiProjectsProjectGet,
  healthApiHealthGet,
  listChunksApiChunksGet,
  listDecisionsApiDecisionsGet,
  listOpenQuestionsApiQuestionsGet,
  listProjectsApiProjectsGet,
  listProvidersApiAuthProvidersGet,
  logoutApiAuthLogoutPost,
  meApiMeGet,
} from './lib/api/hub/sdk.gen';
export type {
  ChunkCountsView,
  ChunkSummary,
  DecisionView,
  FleetSpendView,
  MeResponse,
  MeTenant,
  ProjectView,
  ProviderSummary,
  QuestionView,
} from './lib/api/hub/types.gen';
