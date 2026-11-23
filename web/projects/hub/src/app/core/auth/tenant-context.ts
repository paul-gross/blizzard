import {
  Injectable,
  InjectionToken,
  type Signal,
  inject,
  signal,
} from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';

import { type Membership, type TenantResolution, resolveTenant } from './session.domain';
import { hubMeKey } from '../query-keys';

/** The request header that names the tenant a hub call acts in. */
export const TENANT_HEADER = 'X-Blizzard-Tenant';

/** This tab's tenant — sessionStorage, so two tabs can sit in two tenants. */
export const TAB_TENANT_KEY = 'blizzard.tenant';

/** The tenant this browser last chose — localStorage, so a reload or a new tab opens in it. */
export const LAST_TENANT_KEY = 'blizzard.tenant.last';

/** Reloads the page — the seam a tenant switch re-enters the board through, replaced in tests. */
export const PAGE_RELOAD = new InjectionToken<() => void>('hub.PAGE_RELOAD', {
  providedIn: 'root',
  factory: () => () => globalThis.location.reload(),
});

/**
 * The board's tenant: the one value every hub call names in {@link TENANT_HEADER}, and the selection the app root
 * settles above the shell before any view renders.
 *
 * It opens on this tab's stored tenant, else the browser's last choice, so the first calls of a reload already name it;
 * the app root then {@link enter}s the tenant `/api/me`'s memberships settle on. Storage only seeds and remembers the
 * selection — a browser that refuses storage still holds it for the page's life.
 */
@Injectable({ providedIn: 'root' })
export class TenantContext {
  private readonly queryClient = inject(QueryClient);
  private readonly reload = inject(PAGE_RELOAD);

  private readonly lastChoice = signal(
    read(globalThis.localStorage, LAST_TENANT_KEY),
  );
  private readonly held = signal(
    read(globalThis.sessionStorage, TAB_TENANT_KEY) ?? this.lastChoice(),
  );

  /** The tenant this tab's calls name, `null` before one is known (the hub then settles a single membership itself). */
  readonly current: Signal<string | null> = this.held.asReadonly();

  /** Settles `tenants` against this tab's tenant and the browser's last choice — see {@link resolveTenant}. */
  resolve(tenants: readonly Membership[]): TenantResolution {
    return this.resolver()(tenants);
  }

  /** {@link resolve} with this tab's tenant and the last choice read now, so a reactive caller that builds it tracks
   * both — a query's `select` re-runs when the selection moves. */
  resolver(): (tenants: readonly Membership[]) => TenantResolution {
    const held = this.held();
    const last = this.lastChoice();
    return (tenants) => resolveTenant(tenants, held, last);
  }

  /** Enters `tenant`: this tab's calls name it from now on and the browser remembers it. Entering a tenant other than
   * the one the calls named re-reads every query but `/api/me` — whatever they answered was another tenant's. */
  enter(tenant: string): void {
    this.remember(tenant);
    if (this.held() === tenant) return;
    this.held.set(tenant);
    void this.queryClient.invalidateQueries({
      predicate: (query) =>
        !hubMeKey.every((part, index) => query.queryKey[index] === part),
    });
  }

  /** Switches the board to `tenant`: remembered, then the page reloads into it, so no read, cache entry or stream
   * position of the tenant left behind survives. */
  switchTo(tenant: string): void {
    this.remember(tenant);
    this.held.set(tenant);
    this.reload();
  }

  /** Leaves `tenant`, which the caller's memberships no longer name — deleted, or the membership revoked: neither
   * store remembers it any more, the calls stop naming it, and the page reloads into the resolution a fresh start
   * makes (the only membership left, the picker, or the no-access screen), so nothing read in it survives. */
  leave(tenant: string): void {
    forget(globalThis.sessionStorage, TAB_TENANT_KEY, tenant);
    forget(globalThis.localStorage, LAST_TENANT_KEY, tenant);
    if (this.lastChoice() === tenant) this.lastChoice.set(null);
    if (this.held() === tenant) this.held.set(null);
    this.reload();
  }

  /** Forgets this tab's tenant and the browser's last choice — for a resolution that settled on neither, so no stale
   * id is remembered or named. */
  forgetSelection(): void {
    for (const tenant of new Set([this.held(), this.lastChoice()])) {
      if (tenant === null) continue;
      forget(globalThis.sessionStorage, TAB_TENANT_KEY, tenant);
      forget(globalThis.localStorage, LAST_TENANT_KEY, tenant);
    }
    this.lastChoice.set(null);
    this.held.set(null);
  }

  private remember(tenant: string): void {
    write(globalThis.sessionStorage, TAB_TENANT_KEY, tenant);
    write(globalThis.localStorage, LAST_TENANT_KEY, tenant);
    this.lastChoice.set(tenant);
  }
}

function read(storage: Storage | undefined, key: string): string | null {
  try {
    return storage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

/** Removes `key` while it still names `tenant` — another tab may have stored a different choice since. */
function forget(
  storage: Storage | undefined,
  key: string,
  tenant: string,
): void {
  try {
    if (storage?.getItem(key) === tenant) storage.removeItem(key);
  } catch {
    // A browser refusing storage has nothing stored to forget.
  }
}

function write(storage: Storage | undefined, key: string, value: string): void {
  try {
    storage?.setItem(key, value);
  } catch {
    // A browser refusing storage keeps the selection in memory for this page.
  }
}
