import { type Signal, computed, inject } from '@angular/core';

import type { MeTenant } from 'fleet/shell';
import { injectMeQuery } from '../../core/auth/me.query';
import { TenantContext } from '../../core/auth/tenant-context';

/** What a shell menu needs to offer the tenant switcher: the caller's memberships, the one the board is in, and the
 * switch itself. */
export interface TenantSwitch {
  /** Every membership `/api/me` names. */
  readonly memberships: Signal<readonly MeTenant[]>;
  /** The membership the board acts in, `null` before the tenant is settled. */
  readonly current: Signal<MeTenant | null>;
  /** Whether there is anything to switch to — only a caller with several memberships is offered the switcher. */
  readonly switchable: Signal<boolean>;
  /** Re-enters the board in `tenantId`; choosing the tenant the board is already in does nothing. */
  switchTo(tenantId: string): void;
}

/** The tenant switcher's state, read from `/api/me` and the board's {@link TenantContext}. Switching goes through
 * {@link TenantContext.switchTo}: the tab's tenant and the browser's last choice move to it, and the page reloads the
 * current view in it. */
export function injectTenantSwitch(): TenantSwitch {
  const me = injectMeQuery();
  const tenant = inject(TenantContext);
  const memberships = computed<readonly MeTenant[]>(() => me.data()?.tenants ?? []);
  const current = computed(() => memberships().find((m) => m.tenant_id === tenant.current()) ?? null);
  return {
    memberships,
    current,
    switchable: computed(() => memberships().length > 1),
    switchTo: (tenantId) => {
      if (tenantId !== tenant.current()) tenant.switchTo(tenantId);
    },
  };
}
