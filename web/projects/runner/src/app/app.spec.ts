import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { App } from './app';
import { routes } from './app.routes';

describe('runner App', () => {
  const previousFetch = globalThis.fetch;

  beforeEach(async () => {
    // The shell mounts `LocalPanel`, which now polls `GET /api/leases` (issue #28) —
    // stub a minimal empty response so this shell-level test stays independent of
    // the local panel's own query behavior (covered by `local-panel`'s own specs).
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        // `LocalPanel` now binds its selection to the URL's query params (issue #99),
        // so it injects the router — the shell test wires the real route table.
        provideRouter(routes),
      ],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = previousFetch;
  });

  it('renders the local-panel shell', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('local-panel')).toBeTruthy();
    expect(el.querySelector('[data-testid="local-panel"]')).toBeTruthy();
  });

  it('resolves a selection query-param URL through the real route table and still mounts the panel', async () => {
    // The panel's selection rides in the URL's query params (issue #99). The
    // single catch-all route (`app.routes.ts`) must resolve that always-`''` path
    // — bare, or carrying `?chunk=…&attempt=…` — cleanly; a deep-linked reload
    // lands here. `navigateByUrl` resolves `true` only on a successful match, so
    // this proves the route table itself, not just that the panel renders.
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();

    const resolved = await router.navigateByUrl('/?chunk=ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9&attempt=lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
    await fixture.whenStable();

    expect(resolved).toBe(true);
    expect((fixture.nativeElement as HTMLElement).querySelector('local-panel')).toBeTruthy();
  });
});
