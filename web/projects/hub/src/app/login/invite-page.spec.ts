import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { InvitePage } from './invite-page';

describe('InvitePage', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  async function mount(providers: unknown, token = 'tok_invited') {
    stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/auth/providers' ? providers : {}));
    await TestBed.configureTestingModule({
      imports: [InvitePage],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: convertToParamMap({ token }) } } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(InvitePage);
    await settle(fixture);
    return fixture.nativeElement as HTMLElement;
  }

  it('starts each provider sign-in carrying the invitation token', async () => {
    const el = await mount([
      { name: 'github', display_name: 'GitHub', type: 'github' },
      { name: 'oidc-co', display_name: 'Stub SSO', type: 'oidc' },
    ]);

    expect(el.querySelector('[data-testid="login-provider-github"]')?.getAttribute('href')).toBe(
      '/api/auth/github/authorize?return_to=%2F&invite=tok_invited',
    );
    expect(el.querySelector('[data-testid="login-provider-oidc-co"]')?.getAttribute('href')).toBe(
      '/api/auth/oidc-co/authorize?return_to=%2F&invite=tok_invited',
    );
  });

  it('tells the person which account accepts the invitation', async () => {
    const el = await mount([{ name: 'github', display_name: 'GitHub', type: 'github' }]);

    expect(el.querySelector('[data-testid="invite-page"]')?.textContent).toContain("You've been invited");
    expect(el.querySelector('[data-testid="invite-page-notice"]')?.textContent).toContain(
      'Sign in with the account that uses the invited email address to accept.',
    );
  });

  it('renders the empty state when no providers are configured', async () => {
    const el = await mount([]);

    expect(el.querySelector('[data-testid="invite-no-providers"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="login-providers"]')).toBeNull();
  });
});
