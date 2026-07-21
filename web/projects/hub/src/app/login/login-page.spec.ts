import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { settle } from 'fleet/testing';

import { LoginPage } from './login-page';

describe('LoginPage', () => {
  afterEach(() => {
    hubClient.setConfig({ baseUrl: '', fetch: undefined });
    localStorage.clear();
    sessionStorage.clear();
  });

  async function mount(providers: unknown) {
    hubClient.setConfig({
      baseUrl: 'http://localhost',
      fetch: (async (input: Request) => {
        const url = new URL(input.url);
        if (url.pathname === '/api/auth/providers') {
          return new Response(JSON.stringify(providers), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      }) as typeof fetch,
    });
    await TestBed.configureTestingModule({
      imports: [LoginPage],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(LoginPage);
    await settle(fixture);
    return fixture;
  }

  it('renders a login button per configured provider', async () => {
    const fixture = await mount([
      { name: 'github', display_name: 'GitHub', type: 'github' },
      { name: 'oidc-co', display_name: 'Stub SSO', type: 'oidc' },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="login-provider-github"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="login-provider-oidc-co"]')).toBeTruthy();
  });

  it('renders the empty state when no providers are configured', async () => {
    const fixture = await mount([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="login-no-providers"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="login-providers"]')).toBeNull();
  });

  it('appends the stashed return_to route to every provider link', async () => {
    sessionStorage.setItem('fleet.auth.return-to', '/graphs/gr_1');
    const fixture = await mount([{ name: 'github', display_name: 'GitHub', type: 'github' }]);
    const el = fixture.nativeElement as HTMLElement;

    const href = el.querySelector('[data-testid="login-provider-github"]')?.getAttribute('href');
    expect(href).toBe('/api/auth/github/authorize?return_to=%2Fgraphs%2Fgr_1');
  });

  it('remembers the last-used provider across a remount', async () => {
    const providers = [
      { name: 'github', display_name: 'GitHub', type: 'github' },
      { name: 'oidc-co', display_name: 'Stub SSO', type: 'oidc' },
    ];
    const fixture = await mount(providers);
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="login-provider-oidc-co"]')?.click();
    await settle(fixture);

    TestBed.resetTestingModule();
    const second = await mount(providers);
    const el = second.nativeElement as HTMLElement;
    const items = Array.from(el.querySelectorAll('[data-testid^="login-provider-"][data-provider-type]'));
    expect(items[0].getAttribute('data-testid')).toBe('login-provider-oidc-co');
  });
});
