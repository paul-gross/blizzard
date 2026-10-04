/*
 * Eager-shell entry point of the `fleet` shared library (`fleet/shell`):
 * named re-exports of exactly what an app's eager shell statically reaches
 * (`bzh:frontend-eager-shell-entry`, gated by `web:bundle-composition`).
 */

export { AppShell } from './lib/shell/app-shell/app-shell';
export { BoardHeader } from './lib/shell/board-header/board-header';
export { FleetLiveUpdates } from './lib/sse/fleet-live';

export { ViewportService } from './lib/core/viewport/viewport-service';
export { matchesMobileViewport } from './lib/core/viewport/matches-mobile-viewport';
export { provideViewportRenavigation } from './lib/core/viewport/viewport-renavigation';

export { KitTab, KitTabStrip } from './lib/kit/kit-tab';
export { MobileTabBar, type MobileTabItem } from './lib/shell/mobile-chrome/mobile-tab-bar';

export { KitButton } from './lib/kit/kit-button';
export { LIVE_COVERED_POLL_BACKSTOP_MS } from './lib/core/polling';
export {
  hubAuthProvidersKey,
  hubBoardChunksKey,
  hubChunkCountsKey,
  hubDecisionsKey,
  hubFleetSpendKey,
  hubHealthKey,
  hubMeKey,
  hubQuestionsKey,
} from './lib/core/query-keys';
export { client as hubClient } from './lib/api/hub/client.gen';
export {
  chunkCountsApiChunkCountsGet,
  fleetSpendApiSpendGet,
  healthApiHealthGet,
  listChunksApiChunksGet,
  listDecisionsApiDecisionsGet,
  listOpenQuestionsApiQuestionsGet,
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
  ProviderSummary,
  QuestionView,
} from './lib/api/hub/types.gen';
