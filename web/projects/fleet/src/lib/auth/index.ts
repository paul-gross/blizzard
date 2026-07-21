/*
 * `auth/`'s sub-barrel (issue #93, `bzh:frontend-disjoint-diffs`) — the login/session
 * feature's public surface, re-exported one line from the root `public-api.ts`.
 */

export { injectMeQuery, hasPermission } from './me.query';
export { injectAuthProvidersQuery } from './providers.query';
export { injectLogoutMutation } from './logout.mutation';
export { redirectToLogin, consumeReturnUrl } from './auth-redirect';
export { provideAuthInterceptor } from './auth.interceptor';
export { LoginButtons } from './login-buttons';
export { GuestLobby } from './guest-lobby';
export type { MeResponse, ProviderSummary } from '../api/hub';
