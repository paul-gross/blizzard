import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type MeResponse, hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { LAST_TENANT_KEY, PAGE_RELOAD, TAB_TENANT_KEY } from '../../core/auth/tenant-context';
import { AppNavMenu } from './app-nav-menu';

/** A caller holding two memberships — the only caller the switcher is offered to. */
const TWO_TENANTS: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  tenants: [
    { tenant_id: 'ten_acme', name: 'Acme', role: 'admin', permissions: ['fleet:view'] },
    { tenant_id: 'ten_globex', name: 'Globex', role: 'guest', permissions: ['fleet:view'] },
  ],
};

/** The CDK renders each menu into an overlay on `document.body`, outside the
 * fixture's own element. */
const inOverlay = (selector: string) => document.body.querySelector<HTMLElement>(selector);

describe('AppNavMenu', () => {
  let me: MeResponse;
  let stub: RequestClientStub;
  let reload: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    localStorage.clear();
    sessionStorage.clear();
    me = OPERATOR_ME_RESPONSE;
    stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/me' ? me : {}));
    reload = vi.fn();
    await TestBed.configureTestingModule({
      imports: [AppNavMenu],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: PAGE_RELOAD, useValue: reload },
      ],
    }).compileComponents();
  });
  afterEach(() => stub.restore());

  /** Mounts the menu in `ten_acme` and opens it. */
  async function openIn(identity: MeResponse) {
    me = identity;
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_acme');
    const fixture = TestBed.createComponent(AppNavMenu);
    await settle(fixture);
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await settle(fixture);
    return fixture;
  }

  it('renders the avatar trigger, closed by default', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="app-nav-menu"] fleet-kit-avatar')).not.toBeNull();
    expect(inOverlay('[data-testid="nav-logout"]')).toBeNull();
    expect(inOverlay('[data-testid="nav-appearance"]')).toBeNull();
  });

  it('reveals Log out and the Appearance submenu item once opened', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();

    expect(inOverlay('[data-testid="app-nav-menu-panel"] [data-testid="nav-logout"]')).not.toBeNull();
    expect(inOverlay('[data-testid="app-nav-menu-panel"] [data-testid="nav-appearance"]')).not.toBeNull();
    // The switcher is a submenu, so it stays closed until its own item is entered.
    expect(inOverlay('[data-testid="nav-appearance-panel"]')).toBeNull();
  });

  it('disables Log out while the logout is in flight', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    fixture.componentRef.setInput('logoutPending', true);
    await fixture.whenStable();

    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();

    expect(inOverlay('[data-testid="nav-logout"]')?.getAttribute('aria-disabled')).toBe('true');
  });

  it('opens the appearance switcher as a submenu of the profile menu (issue #161)', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();
    inOverlay('[data-testid="nav-appearance"]')?.click();
    await fixture.whenStable();

    expect(inOverlay('[data-testid="nav-appearance-panel"] [data-testid="viewport-menu-auto"]')).not.toBeNull();
    expect(inOverlay('[data-testid="nav-appearance-panel"] [data-testid="viewport-menu-mobile"]')).not.toBeNull();
    expect(inOverlay('[data-testid="nav-appearance-panel"] [data-testid="viewport-menu-desktop"]')).not.toBeNull();
    // The parent menu stays open behind it — a submenu, not a replacement.
    expect(inOverlay('[data-testid="app-nav-menu-panel"]')).not.toBeNull();
  });

  it('emits logout when the Log out entry is triggered', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    const logout = vi.fn();
    fixture.componentInstance.logout.subscribe(logout);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();
    inOverlay('[data-testid="nav-logout"]')?.click();

    expect(logout).toHaveBeenCalledTimes(1);
  });

  it('offers no tenant switcher to a caller with one membership', async () => {
    await openIn(OPERATOR_ME_RESPONSE);

    expect(inOverlay('[data-testid="app-nav-menu-panel"] [data-testid="nav-appearance"]')).not.toBeNull();
    expect(inOverlay('[data-testid="nav-tenant"]')).toBeNull();
  });

  it('offers every membership in the switcher to a caller with several, the current one checked', async () => {
    const fixture = await openIn(TWO_TENANTS);
    inOverlay('[data-testid="nav-tenant"]')?.click();
    await settle(fixture);

    const acme = inOverlay('[data-testid="nav-tenant-panel"] [data-testid="nav-tenant-panel-ten_acme"]');
    const globex = inOverlay('[data-testid="nav-tenant-panel"] [data-testid="nav-tenant-panel-ten_globex"]');
    expect(acme?.textContent).toContain('Acme');
    expect(acme?.textContent).toContain('ten_acme');
    expect(acme?.getAttribute('aria-checked')).toBe('true');
    expect(globex?.getAttribute('aria-checked')).toBe('false');
  });

  it('switching sets the tab tenant and the last choice, then reloads the view, with no sign-in', async () => {
    const fixture = await openIn(TWO_TENANTS);
    inOverlay('[data-testid="nav-tenant"]')?.click();
    await settle(fixture);
    inOverlay('[data-testid="nav-tenant-panel-ten_globex"]')?.click();

    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBe('ten_globex');
    expect(localStorage.getItem(LAST_TENANT_KEY)).toBe('ten_globex');
    expect(reload).toHaveBeenCalledTimes(1);
    expect(stub.requests.filter((r) => r.path.startsWith('/api/auth'))).toEqual([]);
  });

  it('choosing the tenant the board is already in reloads nothing', async () => {
    const fixture = await openIn(TWO_TENANTS);
    inOverlay('[data-testid="nav-tenant"]')?.click();
    await settle(fixture);
    inOverlay('[data-testid="nav-tenant-panel-ten_acme"]')?.click();

    expect(reload).not.toHaveBeenCalled();
  });
});
