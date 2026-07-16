import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { App } from './app';

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

    expect(el.querySelector('fleet-local-panel')).toBeTruthy();
    expect(el.querySelector('[data-testid="local-panel"]')).toBeTruthy();
  });
});
