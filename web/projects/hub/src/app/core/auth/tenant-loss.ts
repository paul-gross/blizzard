import { Injectable, inject } from '@angular/core';

import { FLEET_CLOCK } from 'fleet/shell';

import { readMe } from './me.query';
import { TenantContext } from './tenant-context';

/** How long `/api/me` naming the board's tenant settles that it is still there: a 404 inside the window is a missing
 * record in a live tenant and asks nothing, so a view polling a gone record never re-reads `/api/me` on every answer. */
export const TENANT_CONFIRMED_MS = 15_000;

/**
 * Notices the board losing its tenant mid-session — the tenant deleted, or the caller's membership in it revoked.
 *
 * The hub answers 404 to a tenant-scoped call naming a tenant that is gone and to one the caller cannot enter, alike,
 * and also to a missing record in a live tenant; the 404 alone tells them apart by nothing the board may read. So a
 * 404 to a call that named the board's tenant only prompts a fresh read of `/api/me`, the caller's memberships: while
 * they still name the tenant nothing changes, and once they do not the board {@link TenantContext.leave}s it and
 * resolves again as at startup.
 *
 * One read answers every 404 that arrives while it is in flight, and a read that found the tenant still there answers
 * every 404 for {@link TENANT_CONFIRMED_MS} after it.
 */
@Injectable({ providedIn: 'root' })
export class TenantLoss {
  private readonly context = inject(TenantContext);
  private readonly clock = inject(FLEET_CLOCK);

  private checking = false;
  private confirmed: { readonly tenant: string; readonly at: number } | null = null;

  /** A call that named `tenant` answered 404. */
  notFound(tenant: string): void {
    // A call sent before the board entered another tenant, or after it left this one, says nothing about the current.
    if (this.checking || tenant !== this.context.current()) return;
    if (this.confirmed?.tenant === tenant && this.clock() - this.confirmed.at < TENANT_CONFIRMED_MS) return;
    this.checking = true;
    void this.check(tenant)
      .catch(() => undefined)
      .finally(() => (this.checking = false));
  }

  private async check(tenant: string): Promise<void> {
    // Read past the query cache: the board must not re-resolve on these memberships before it has left the tenant.
    // No session is the sign-in flow's to handle, and a failed read (thrown) leaves the board as it is.
    const me = await readMe();
    if (me === null) return;
    if ((me.tenants ?? []).some((membership) => membership.tenant_id === tenant)) {
      this.confirmed = { tenant, at: this.clock() };
      return;
    }
    if (tenant === this.context.current()) this.context.leave(tenant);
  }
}
