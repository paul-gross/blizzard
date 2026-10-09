import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type MeResponse, hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { LAST_TENANT_KEY, PAGE_RELOAD, TAB_TENANT_KEY } from '../../core/auth/tenant-context';
import { MobileTitlebar } from './mobile-titlebar';

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

describe('MobileTitlebar', () => {
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
      imports: [MobileTitlebar],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: PAGE_RELOAD, useValue: reload },
      ],
    }).compileComponents();
  });
  afterEach(() => stub.restore());

  /** Mounts the titlebar in `ten_acme` for `identity`. */
  async function mountIn(identity: MeResponse) {
    me = identity;
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_acme');
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    await settle(fixture);
    return fixture;
  }

  it('renders the brand and no board/graphs nav — navigation lives in the bottom tab bar', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-titlebar"]')?.textContent).toContain('blizzard');
    expect(el.querySelector('[data-testid="nav-board"]')).toBeNull();
    expect(el.querySelector('[data-testid="nav-graphs"]')).toBeNull();
  });

  it('reflects the live input on the live dot', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', false);
    await fixture.whenStable();
    let el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="mobile-titlebar-livedot"]')?.classList.contains('active')).toBe(false);

    fixture.componentRef.setInput('live', true);
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="mobile-titlebar-livedot"]')?.classList.contains('active')).toBe(true);
  });

  it('buries the appearance switcher behind the overflow menu, closed by default', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(inOverlay('[data-testid="mobile-titlebar-appearance"]')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="mobile-titlebar-menu"]')?.click();
    await fixture.whenStable();

    expect(
      inOverlay('[data-testid="mobile-titlebar-menu-panel"] [data-testid="mobile-titlebar-appearance"]'),
    ).not.toBeNull();

    inOverlay('[data-testid="mobile-titlebar-appearance"]')?.click();
    await fixture.whenStable();

    expect(
      inOverlay('[data-testid="mobile-titlebar-appearance-panel"] [data-testid="viewport-menu-auto"]'),
    ).not.toBeNull();
  });

  it('names the tenant the board is in beside the wordmark', async () => {
    const fixture = await mountIn(TWO_TENANTS);

    const caption = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="mobile-titlebar-caption"]');
    expect(caption?.textContent?.trim()).toBe('Acme');
  });

  it('offers no tenant switcher to a caller with one membership', async () => {
    const fixture = await mountIn({
      ...OPERATOR_ME_RESPONSE,
      tenants: [{ tenant_id: 'ten_acme', name: 'Acme', role: 'admin', permissions: ['fleet:view'] }],
    });
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="mobile-titlebar-menu"]')?.click();
    await settle(fixture);

    expect(inOverlay('[data-testid="mobile-titlebar-appearance"]')).not.toBeNull();
    expect(inOverlay('[data-testid="mobile-titlebar-tenant"]')).toBeNull();
  });

  it('switches tenant from the overflow menu: tab tenant and last choice set, the view reloaded', async () => {
    const fixture = await mountIn(TWO_TENANTS);
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="mobile-titlebar-menu"]')?.click();
    await settle(fixture);
    inOverlay('[data-testid="mobile-titlebar-tenant"]')?.click();
    await settle(fixture);

    expect(inOverlay('[data-testid="mobile-titlebar-tenant-panel-ten_acme"]')?.getAttribute('aria-checked')).toBe('true');
    inOverlay('[data-testid="mobile-titlebar-tenant-panel-ten_globex"]')?.click();

    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBe('ten_globex');
    expect(localStorage.getItem(LAST_TENANT_KEY)).toBe('ten_globex');
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
