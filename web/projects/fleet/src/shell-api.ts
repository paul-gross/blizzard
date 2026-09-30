/*
 * Eager-shell entry point of the `fleet` shared library (`fleet/shell`,
 * `architecture/frontend-structure/eager-shell.md`, `bzh:frontend-eager-shell-entry`).
 *
 * Named re-exports, file by file, of exactly what an app's eager shell statically
 * reaches — rather than the root `fleet` barrel, which re-exports every feature area
 * whole and esbuild cannot tree-shake even when nothing on the eager path reads a given
 * export. Anything reached only lazily imports the root `fleet` barrel instead.
 */

export { AppShell } from './lib/app-shell/app-shell';
export { BoardHeader } from './lib/board-header/board-header';
export { FleetLiveUpdates } from './lib/sse/fleet-live';

export { PendingLobby } from './lib/auth/pending-lobby';
export { hasPermission, injectMeQuery } from './lib/auth/me.query';
export { injectAuthProvidersQuery } from './lib/auth/providers.query';
export { injectLogoutMutation } from './lib/auth/logout.mutation';
export { redirectToLogin } from './lib/auth/auth-redirect';
export { provideAuthInterceptor } from './lib/auth/auth.interceptor';

export { ViewportService } from './lib/viewport/viewport-service';
export { matchesMobileViewport } from './lib/viewport/matches-mobile-viewport';
export { provideViewportRenavigation } from './lib/viewport/viewport-renavigation';

export { injectHubChunksQuery } from './lib/chunks/chunks.query';
export { injectHubFleetSpendQuery } from './lib/fleet-spend/fleet-spend.query';
export { injectHubHealthQuery } from './lib/health/health.query';
export { injectHubQuestionsQuery } from './lib/questions/questions.query';

export { KitTab, KitTabStrip } from './lib/kit/kit-tab';
export { MobileTabBar, type MobileTabItem } from './lib/mobile-chrome/mobile-tab-bar';
