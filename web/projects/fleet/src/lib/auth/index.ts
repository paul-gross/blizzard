/*
 * `auth/`'s sub-barrel (`bzh:frontend-disjoint-diffs`) — the login/session
 * feature's public surface, re-exported one line from the root `public-api.ts`.
 */

export { injectMeQuery, hasPermission } from './me.query';
export { injectAuthProvidersQuery } from './providers.query';
export { consumeReturnUrl, safeAuthorizeReturnTo } from './auth-redirect';
export { LoginButtons } from './login-buttons';
export type { MeResponse, ProviderSummary } from '../api/hub';
