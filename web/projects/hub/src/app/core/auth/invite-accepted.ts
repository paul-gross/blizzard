import { inject } from '@angular/core';
import { type CanActivateFn, Router } from '@angular/router';

import { TenantContext } from './tenant-context';

/**
 * `/invite/accepted?tenant=<tenant_id>` — where the hub lands a sign-in that accepted an invitation. It enters the
 * invited tenant, so this tab and the browser's last choice name it, and replaces itself with the board's root: the
 * person lands in the tenant they were invited to, whatever other memberships they hold.
 *
 * The id is only a selection. A tenant the person holds no membership in settles like any stale choice does — their
 * only membership, else the picker — and every call is still checked against their memberships by the hub.
 */
export const enterInvitedTenant: CanActivateFn = (route) => {
  const tenant = route.queryParamMap.get('tenant');
  if (tenant) inject(TenantContext).enter(tenant);
  return inject(Router).createUrlTree(['/']);
};
