import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { LocalIdentity } from './local-identity';

/** Render `LocalIdentity` with `GET /api/auth/session` answered by `session` and
 * `POST /api/auth/logout` a 204 no-op. */
async function render(session: unknown) {
  const stub = stubRequestClient(runnerClient, (method, path) => {
    if (method === 'GET' && path === '/api/auth/session') return session;
    if (method === 'POST' && path === '/api/auth/logout') return {};
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [LocalIdentity],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalIdentity);
  // Never actually navigate the jsdom window on logout.
  vi.spyOn(fixture.componentInstance as unknown as { reload: () => void }, 'reload').mockImplementation(() => undefined);
  await settle(fixture);
  return { fixture, stub };
}

describe('LocalIdentity', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  it('renders the signed-in hub username and a logout control under an oauth-mode hub', async () => {
    const { fixture, stub: s } = await render({ auth_enabled: true, username: 'alice' });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-identity"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="identity-username"]')?.textContent).toContain('alice');
    expect(el.querySelector('[data-testid="identity-logout"]')).not.toBeNull();
  });

  it('renders nothing under a none-mode hub (authless surface)', async () => {
    const { fixture, stub: s } = await render({ auth_enabled: false, username: null });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-identity"]')).toBeNull();
    expect(el.querySelector('[data-testid="identity-logout"]')).toBeNull();
  });

  it('renders nothing under oauth when no session resolves (username null)', async () => {
    const { fixture, stub: s } = await render({ auth_enabled: true, username: null });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-identity"]')).toBeNull();
  });

  it('POSTs /api/auth/logout and reloads when the logout control is activated', async () => {
    const { fixture, stub: s } = await render({ auth_enabled: true, username: 'alice' });
    stub = s;
    const reload = fixture.componentInstance as unknown as { reload: () => void };
    const reloadSpy = vi.spyOn(reload, 'reload');
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="identity-logout"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/auth/logout', 'POST')).toHaveLength(1);
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });
});
