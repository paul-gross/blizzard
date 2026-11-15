import { type SseStatus } from 'fleet';
import { type MeResponse, type MeTenant, errorMessage } from 'fleet/shell';

import { type TenantResolution } from '../core/auth/tenant-context';
import { viewOf } from '../core/project-lens';

/** Before initial navigation settles, the address bar is the only completed route. */
export function shellUrl(pendingUrl: string | null, navigated: boolean, routerUrl: string, pathname: string): string {
  return pendingUrl ?? (navigated ? routerUrl : pathname);
}

export function shellView(url: string): string {
  return viewOf(url).split('/')[1];
}

/** The session gate, decided above the shell. */
export type AuthState = 'loading' | 'unauthenticated' | 'no-access' | 'choose' | 'ready';

/** The top-level session gate: `loading` while the identity read is pending, `unauthenticated` without an identity
 * (`tenancy` is `null`), then by the caller's memberships — `no-access` with none, `choose` while several leave the
 * tenant to the person, `ready` once a tenant is selected. Only `ready` renders the shell. */
export function authState(isPending: boolean, tenancy: TenantResolution | null): AuthState {
  if (isPending) return 'loading';
  if (tenancy === null) return 'unauthenticated';
  if (tenancy.kind === 'none') return 'no-access';
  if (tenancy.kind === 'choose') return 'choose';
  return 'ready';
}

/** The tenant `resolve` settles from an identity's memberships, `null` without an identity. */
export function tenancyOf(
  me: MeResponse | null,
  resolve: (tenants: readonly MeTenant[]) => TenantResolution,
): TenantResolution | null {
  return me === null ? null : resolve(me.tenants ?? []);
}

/** The selected tenant's name, empty while none is selected. */
export function selectedTenantName(tenancy: TenantResolution | null): string {
  return tenancy?.kind === 'selected' ? tenancy.tenant.name : '';
}

/** The header's connection label: the live stream's reconnect first, then the health read's pending, failed, and
 * reported states — `ok` when the health read names no status. */
export function connectionLabel(
  streamState: SseStatus,
  healthPending: boolean,
  healthError: boolean,
  healthStatus: string | undefined,
): string {
  if (streamState === 'reconnecting') return 'reconnecting…';
  if (healthPending) return 'connecting…';
  if (healthError) return 'offline';
  return healthStatus ?? 'ok';
}

/** The logout failure as the shell renders it, `null` while there is none. */
export function logoutErrorText(error: unknown): string | null {
  return error === null || error === undefined ? null : errorMessage(error, 'Log out failed');
}
